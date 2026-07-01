from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Protocol

from full_python.models import MarketBar, OrderIntent, StrategyResult


class Strategy(Protocol):
    def on_bar(self, bar: MarketBar) -> StrategyResult:
        ...


@dataclass(frozen=True)
class SimulationCosts:
    point_value: float = 2.0
    slippage_points_per_side: float = 1.0
    commission_per_contract: float = 1.0

    def to_assumptions(self) -> dict[str, float]:
        return {
            "point_value": self.point_value,
            "slippage_points_per_side": self.slippage_points_per_side,
            "commission_per_contract": self.commission_per_contract,
        }


@dataclass(frozen=True)
class TradeFill:
    trade_id: str
    symbol: str
    side: str
    quantity: int
    entry_timestamp_utc: str
    entry_price: float
    exit_timestamp_utc: str
    exit_price: float
    exit_reason: str
    stop_price: float
    pnl_points: float
    gross_pnl_dollars: float
    commission_dollars: float
    net_pnl_dollars: float
    max_favorable_excursion_points: float
    max_adverse_excursion_points: float
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _OpenTrade:
    trade_id: str
    symbol: str
    side: str
    quantity: int
    entry_timestamp_utc: str
    entry_price: float
    stop_price: float
    max_favorable_excursion_points: float
    max_adverse_excursion_points: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TradeLedger:
    trades: list[TradeFill]
    ignored_order_intents: int
    assumptions: dict[str, str | float]

    def summary(self) -> dict[str, Any]:
        total_pnl_points = sum(trade.pnl_points for trade in self.trades)
        total_gross_pnl_dollars = sum(trade.gross_pnl_dollars for trade in self.trades)
        total_commission_dollars = sum(trade.commission_dollars for trade in self.trades)
        total_net_pnl_dollars = sum(trade.net_pnl_dollars for trade in self.trades)
        winning_trades = sum(1 for trade in self.trades if trade.pnl_points > 0)
        losing_trades = sum(1 for trade in self.trades if trade.pnl_points < 0)
        exit_reason_counts = Counter(trade.exit_reason for trade in self.trades)
        return {
            "trade_count": len(self.trades),
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "flat_trades": len(self.trades) - winning_trades - losing_trades,
            "win_rate": winning_trades / len(self.trades) if self.trades else 0.0,
            "total_pnl_points": total_pnl_points,
            "average_pnl_points": total_pnl_points / len(self.trades) if self.trades else 0.0,
            "total_gross_pnl_dollars": total_gross_pnl_dollars,
            "total_commission_dollars": total_commission_dollars,
            "total_net_pnl_dollars": total_net_pnl_dollars,
            "average_net_pnl_dollars": total_net_pnl_dollars / len(self.trades) if self.trades else 0.0,
            "exit_reason_counts": dict(sorted(exit_reason_counts.items())),
            "ignored_order_intents": self.ignored_order_intents,
            "assumptions": self.assumptions,
        }


ASSUMPTIONS = {
    "position_model": "one_open_long_position_max",
    "entry_fill": "current_bar_close",
    "stop_fill": "stop_price_when_later_bar_low_touches_stop",
    "symbol_change_exit": "new_contract_bar_open",
    "symbol_change_exit_mode": "next_open",
    "final_exit": "last_bar_close",
}


def simulate_strategy_trades(
    bars: Iterable[MarketBar],
    strategy: Strategy,
    *,
    costs: SimulationCosts | None = None,
    symbol_change_exit_mode: str = "next_open",
) -> TradeLedger:
    if symbol_change_exit_mode not in {"next_open", "previous_close"}:
        raise ValueError(f"Unsupported symbol_change_exit_mode: {symbol_change_exit_mode}")
    active_costs = SimulationCosts() if costs is None else costs
    trades: list[TradeFill] = []
    open_trade: _OpenTrade | None = None
    ignored_order_intents = 0
    last_bar: MarketBar | None = None

    for bar in bars:
        if open_trade is not None and bar.symbol != open_trade.symbol:
            if symbol_change_exit_mode == "previous_close" and last_bar is not None:
                trades.append(
                    _close_trade(open_trade, last_bar.timestamp_utc, last_bar.close, "symbol_change", active_costs)
                )
            else:
                trades.append(_close_trade(open_trade, bar.timestamp_utc, bar.open, "symbol_change", active_costs))
            open_trade = None

        if open_trade is not None and open_trade.side == "long" and bar.low <= open_trade.stop_price:
            open_trade = _update_long_excursion(open_trade, bar)
            trades.append(_close_trade(open_trade, bar.timestamp_utc, open_trade.stop_price, "stop", active_costs))
            open_trade = None
        elif open_trade is not None and open_trade.side == "long":
            open_trade = _update_long_excursion(open_trade, bar)

        result = strategy.on_bar(bar)
        for order_intent in result.order_intents:
            if open_trade is not None:
                ignored_order_intents += 1
                continue
            if order_intent.side != "buy":
                ignored_order_intents += 1
                continue
            open_trade = _open_long_trade(order_intent, bar, len(trades) + 1, active_costs)
        last_bar = bar

    if open_trade is not None and last_bar is not None:
        trades.append(_close_trade(open_trade, last_bar.timestamp_utc, last_bar.close, "end_of_data", active_costs))

    return TradeLedger(
        trades=trades,
        ignored_order_intents=ignored_order_intents,
        assumptions={
            **ASSUMPTIONS,
            "symbol_change_exit": _symbol_change_exit_assumption(symbol_change_exit_mode),
            "symbol_change_exit_mode": symbol_change_exit_mode,
            **active_costs.to_assumptions(),
        },
    )


def _symbol_change_exit_assumption(symbol_change_exit_mode: str) -> str:
    if symbol_change_exit_mode == "previous_close":
        return "previous_contract_last_close"
    return "new_contract_bar_open"


def write_trades_csv(ledger: TradeLedger, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "trade_id",
        "symbol",
        "side",
        "quantity",
        "entry_timestamp_utc",
        "entry_price",
        "exit_timestamp_utc",
        "exit_price",
        "exit_reason",
        "stop_price",
        "pnl_points",
        "gross_pnl_dollars",
        "commission_dollars",
        "net_pnl_dollars",
        "max_favorable_excursion_points",
        "max_adverse_excursion_points",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for trade in ledger.trades:
            payload = trade.to_dict()
            writer.writerow({field: payload[field] for field in fieldnames})


def write_trade_summary_json(ledger: TradeLedger, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(ledger.summary(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _open_long_trade(
    order_intent: OrderIntent,
    bar: MarketBar,
    trade_number: int,
    costs: SimulationCosts,
) -> _OpenTrade:
    stop_price = float(order_intent.metadata["stop_price"])
    return _OpenTrade(
        trade_id=f"trade-{trade_number:08d}",
        symbol=bar.symbol,
        side="long",
        quantity=order_intent.quantity,
        entry_timestamp_utc=bar.timestamp_utc,
        entry_price=bar.close + costs.slippage_points_per_side,
        stop_price=stop_price,
        max_favorable_excursion_points=max(0.0, bar.high - (bar.close + costs.slippage_points_per_side)),
        max_adverse_excursion_points=min(0.0, bar.low - (bar.close + costs.slippage_points_per_side)),
        metadata={"entry_reason": order_intent.reason},
    )


def _update_long_excursion(open_trade: _OpenTrade, bar: MarketBar) -> _OpenTrade:
    return _OpenTrade(
        trade_id=open_trade.trade_id,
        symbol=open_trade.symbol,
        side=open_trade.side,
        quantity=open_trade.quantity,
        entry_timestamp_utc=open_trade.entry_timestamp_utc,
        entry_price=open_trade.entry_price,
        stop_price=open_trade.stop_price,
        max_favorable_excursion_points=max(
            open_trade.max_favorable_excursion_points,
            bar.high - open_trade.entry_price,
        ),
        max_adverse_excursion_points=min(
            open_trade.max_adverse_excursion_points,
            bar.low - open_trade.entry_price,
        ),
        metadata=dict(open_trade.metadata),
    )


def _close_trade(
    open_trade: _OpenTrade,
    exit_timestamp_utc: str,
    exit_price: float,
    exit_reason: str,
    costs: SimulationCosts,
) -> TradeFill:
    adjusted_exit_price = exit_price - costs.slippage_points_per_side
    pnl_points = adjusted_exit_price - open_trade.entry_price
    gross_pnl_dollars = pnl_points * costs.point_value * open_trade.quantity
    commission_dollars = 2 * costs.commission_per_contract * open_trade.quantity
    return TradeFill(
        trade_id=open_trade.trade_id,
        symbol=open_trade.symbol,
        side=open_trade.side,
        quantity=open_trade.quantity,
        entry_timestamp_utc=open_trade.entry_timestamp_utc,
        entry_price=open_trade.entry_price,
        exit_timestamp_utc=exit_timestamp_utc,
        exit_price=adjusted_exit_price,
        exit_reason=exit_reason,
        stop_price=open_trade.stop_price,
        pnl_points=pnl_points,
        gross_pnl_dollars=gross_pnl_dollars,
        commission_dollars=commission_dollars,
        net_pnl_dollars=gross_pnl_dollars - commission_dollars,
        max_favorable_excursion_points=open_trade.max_favorable_excursion_points,
        max_adverse_excursion_points=open_trade.max_adverse_excursion_points,
        metadata=dict(open_trade.metadata),
    )
