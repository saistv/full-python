from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable, Protocol
from zoneinfo import ZoneInfo

from full_python.models import MarketBar, OrderIntent, StrategyResult


NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


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
class ExitConversionConfig:
    mfe_trailing_activation_points: float | None = None
    mfe_trailing_giveback_points: float | None = None

    def __post_init__(self) -> None:
        has_activation = self.mfe_trailing_activation_points is not None
        has_giveback = self.mfe_trailing_giveback_points is not None
        if has_activation != has_giveback:
            raise ValueError("MFE trailing requires both activation and giveback points")
        if self.mfe_trailing_activation_points is not None and self.mfe_trailing_activation_points <= 0:
            raise ValueError("MFE trailing activation points must be positive")
        if self.mfe_trailing_giveback_points is not None and self.mfe_trailing_giveback_points <= 0:
            raise ValueError("MFE trailing giveback points must be positive")

    @property
    def enabled(self) -> bool:
        return self.mfe_trailing_activation_points is not None

    def to_assumptions(self) -> dict[str, str | float]:
        if not self.enabled:
            return {
                "exit_conversion": "none",
            }
        assert self.mfe_trailing_activation_points is not None
        assert self.mfe_trailing_giveback_points is not None
        return {
            "exit_conversion": "mfe_trailing",
            "mfe_trailing_activation_points": self.mfe_trailing_activation_points,
            "mfe_trailing_giveback_points": self.mfe_trailing_giveback_points,
        }


@dataclass(frozen=True)
class ReentryControlConfig:
    cooldown_bars_after_exit: int = 0
    require_fresh_breakout_after_exit: bool = False
    fresh_breakout_clearance_points: float = 0.0

    def __post_init__(self) -> None:
        if self.cooldown_bars_after_exit < 0:
            raise ValueError("cooldown_bars_after_exit must be non-negative")
        if self.fresh_breakout_clearance_points < 0:
            raise ValueError("fresh_breakout_clearance_points must be non-negative")

    def to_assumptions(self) -> dict[str, str | int | float | bool]:
        if self.require_fresh_breakout_after_exit and self.cooldown_bars_after_exit > 0:
            reentry_control = "fresh_breakout_and_cooldown"
        elif self.require_fresh_breakout_after_exit:
            reentry_control = "fresh_breakout"
        elif self.cooldown_bars_after_exit > 0:
            reentry_control = "cooldown"
        else:
            reentry_control = "same_bar_exit_block"
        return {
            "reentry_control": reentry_control,
            "cooldown_bars_after_exit": self.cooldown_bars_after_exit,
            "require_fresh_breakout_after_exit": self.require_fresh_breakout_after_exit,
            "fresh_breakout_clearance_points": self.fresh_breakout_clearance_points,
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
    trailing_stop_price: float | None
    exit_conversion_name: str
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
    trailing_stop_price: float | None
    exit_conversion_name: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _ReentryBreakoutRange:
    high: float
    low: float


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
    "position_model": "one_open_position_max",
    "entry_fill": "current_bar_close",
    "stop_fill": "stop_price_when_later_bar_low_touches_stop",
    "symbol_change_exit": "new_contract_bar_open",
    "symbol_change_exit_mode": "next_open",
    "exit_at_session_end": False,
    "final_exit": "last_bar_close",
}


def simulate_strategy_trades(
    bars: Iterable[MarketBar],
    strategy: Strategy,
    *,
    costs: SimulationCosts | None = None,
    symbol_change_exit_mode: str = "next_open",
    exit_conversion: ExitConversionConfig | None = None,
    reentry_control: ReentryControlConfig | None = None,
    exit_at_session_end: bool = False,
) -> TradeLedger:
    if symbol_change_exit_mode not in {"next_open", "previous_close"}:
        raise ValueError(f"Unsupported symbol_change_exit_mode: {symbol_change_exit_mode}")
    active_costs = SimulationCosts() if costs is None else costs
    active_exit_conversion = ExitConversionConfig() if exit_conversion is None else exit_conversion
    active_reentry_control = ReentryControlConfig() if reentry_control is None else reentry_control
    trades: list[TradeFill] = []
    open_trade: _OpenTrade | None = None
    ignored_order_intents = 0
    last_bar: MarketBar | None = None
    cooldown_bars_remaining = 0
    reentry_breakout_range: _ReentryBreakoutRange | None = None

    for bar in bars:
        exited_this_bar = False
        if (
            exit_at_session_end
            and open_trade is not None
            and last_bar is not None
            and _session_date(bar) != _session_date(last_bar)
        ):
            trades.append(
                _close_trade(open_trade, last_bar.timestamp_utc, last_bar.close, "session_end", active_costs)
            )
            open_trade = None

        if open_trade is not None and bar.symbol != open_trade.symbol:
            if symbol_change_exit_mode == "previous_close" and last_bar is not None:
                trades.append(
                    _close_trade(open_trade, last_bar.timestamp_utc, last_bar.close, "symbol_change", active_costs)
                )
            else:
                trades.append(_close_trade(open_trade, bar.timestamp_utc, bar.open, "symbol_change", active_costs))
            open_trade = None
            exited_this_bar = True
            cooldown_bars_remaining = active_reentry_control.cooldown_bars_after_exit
            reentry_breakout_range = _initial_reentry_breakout_range(bar, active_reentry_control)

        if open_trade is not None and _trailing_stop_touched(open_trade, bar):
            trades.append(
                _close_trade(
                    open_trade,
                    bar.timestamp_utc,
                    open_trade.trailing_stop_price,
                    "mfe_trailing_stop",
                    active_costs,
                )
            )
            open_trade = None
            exited_this_bar = True
            cooldown_bars_remaining = active_reentry_control.cooldown_bars_after_exit
            reentry_breakout_range = _initial_reentry_breakout_range(bar, active_reentry_control)
        elif open_trade is not None and _stop_touched(open_trade, bar):
            open_trade = _update_excursion(open_trade, bar)
            trades.append(_close_trade(open_trade, bar.timestamp_utc, open_trade.stop_price, "stop", active_costs))
            open_trade = None
            exited_this_bar = True
            cooldown_bars_remaining = active_reentry_control.cooldown_bars_after_exit
            reentry_breakout_range = _initial_reentry_breakout_range(bar, active_reentry_control)
        elif open_trade is not None:
            open_trade = _update_excursion(open_trade, bar)
            open_trade = _update_mfe_trailing_stop(open_trade, active_exit_conversion)

        result = strategy.on_bar(bar)
        for order_intent in result.order_intents:
            if open_trade is not None:
                ignored_order_intents += 1
                continue
            if exited_this_bar or cooldown_bars_remaining > 0:
                ignored_order_intents += 1
                continue
            if _blocked_by_fresh_breakout_gate(bar, order_intent.side, reentry_breakout_range, active_reentry_control):
                ignored_order_intents += 1
                continue
            if order_intent.side not in {"buy", "sell"}:
                ignored_order_intents += 1
                continue
            open_trade = _open_trade(order_intent, bar, len(trades) + 1, active_costs)
            reentry_breakout_range = None
        if open_trade is None and not exited_this_bar and cooldown_bars_remaining > 0:
            cooldown_bars_remaining -= 1
        if open_trade is None and not exited_this_bar and reentry_breakout_range is not None:
            reentry_breakout_range = _update_reentry_breakout_range(reentry_breakout_range, bar)
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
            "exit_at_session_end": exit_at_session_end,
            **active_costs.to_assumptions(),
            **active_exit_conversion.to_assumptions(),
            **active_reentry_control.to_assumptions(),
        },
    )


def _session_date(bar: MarketBar) -> str:
    return _parse_utc_timestamp(bar.timestamp_utc).astimezone(NEW_YORK).date().isoformat()


def _parse_utc_timestamp(timestamp_utc: str) -> datetime:
    if timestamp_utc.endswith("Z"):
        timestamp_utc = f"{timestamp_utc[:-1]}+00:00"
    parsed = datetime.fromisoformat(timestamp_utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _symbol_change_exit_assumption(symbol_change_exit_mode: str) -> str:
    if symbol_change_exit_mode == "previous_close":
        return "previous_contract_last_close"
    return "new_contract_bar_open"


def _initial_reentry_breakout_range(
    bar: MarketBar,
    reentry_control: ReentryControlConfig,
) -> _ReentryBreakoutRange | None:
    if not reentry_control.require_fresh_breakout_after_exit:
        return None
    return _ReentryBreakoutRange(high=bar.high, low=bar.low)


def _update_reentry_breakout_range(
    reentry_range: _ReentryBreakoutRange,
    bar: MarketBar,
) -> _ReentryBreakoutRange:
    return _ReentryBreakoutRange(
        high=max(reentry_range.high, bar.high),
        low=min(reentry_range.low, bar.low),
    )


def _blocked_by_fresh_breakout_gate(
    bar: MarketBar,
    order_side: str,
    reentry_breakout_range: _ReentryBreakoutRange | None,
    reentry_control: ReentryControlConfig,
) -> bool:
    if reentry_breakout_range is None:
        return False
    if order_side == "buy":
        return bar.close <= reentry_breakout_range.high + reentry_control.fresh_breakout_clearance_points
    if order_side == "sell":
        return bar.close >= reentry_breakout_range.low - reentry_control.fresh_breakout_clearance_points
    return True


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
        "trailing_stop_price",
        "exit_conversion_name",
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


def _open_trade(
    order_intent: OrderIntent,
    bar: MarketBar,
    trade_number: int,
    costs: SimulationCosts,
) -> _OpenTrade:
    stop_price = float(order_intent.metadata["stop_price"])
    trade_side = "long" if order_intent.side == "buy" else "short"
    entry_price = (
        bar.close + costs.slippage_points_per_side
        if trade_side == "long"
        else bar.close - costs.slippage_points_per_side
    )
    return _OpenTrade(
        trade_id=f"trade-{trade_number:08d}",
        symbol=bar.symbol,
        side=trade_side,
        quantity=order_intent.quantity,
        entry_timestamp_utc=bar.timestamp_utc,
        entry_price=entry_price,
        stop_price=stop_price,
        max_favorable_excursion_points=_bar_mfe_points(trade_side, entry_price, bar),
        max_adverse_excursion_points=_bar_mae_points(trade_side, entry_price, bar),
        trailing_stop_price=None,
        exit_conversion_name="none",
        metadata={"entry_reason": order_intent.reason},
    )


def _trailing_stop_touched(open_trade: _OpenTrade, bar: MarketBar) -> bool:
    if open_trade.trailing_stop_price is None:
        return False
    if open_trade.side == "long":
        return bar.low <= open_trade.trailing_stop_price
    return bar.high >= open_trade.trailing_stop_price


def _stop_touched(open_trade: _OpenTrade, bar: MarketBar) -> bool:
    if open_trade.side == "long":
        return bar.low <= open_trade.stop_price
    return bar.high >= open_trade.stop_price


def _update_excursion(open_trade: _OpenTrade, bar: MarketBar) -> _OpenTrade:
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
            _bar_mfe_points(open_trade.side, open_trade.entry_price, bar),
        ),
        max_adverse_excursion_points=min(
            open_trade.max_adverse_excursion_points,
            _bar_mae_points(open_trade.side, open_trade.entry_price, bar),
        ),
        trailing_stop_price=open_trade.trailing_stop_price,
        exit_conversion_name=open_trade.exit_conversion_name,
        metadata=dict(open_trade.metadata),
    )


def _update_mfe_trailing_stop(
    open_trade: _OpenTrade,
    exit_conversion: ExitConversionConfig,
) -> _OpenTrade:
    if not exit_conversion.enabled:
        return open_trade
    assert exit_conversion.mfe_trailing_activation_points is not None
    assert exit_conversion.mfe_trailing_giveback_points is not None
    if open_trade.max_favorable_excursion_points < exit_conversion.mfe_trailing_activation_points:
        return open_trade
    if open_trade.side == "long":
        candidate_stop = (
            open_trade.entry_price
            + open_trade.max_favorable_excursion_points
            - exit_conversion.mfe_trailing_giveback_points
        )
        trailing_stop_price = (
            candidate_stop
            if open_trade.trailing_stop_price is None
            else max(open_trade.trailing_stop_price, candidate_stop)
        )
    else:
        candidate_stop = (
            open_trade.entry_price
            - open_trade.max_favorable_excursion_points
            + exit_conversion.mfe_trailing_giveback_points
        )
        trailing_stop_price = (
            candidate_stop
            if open_trade.trailing_stop_price is None
            else min(open_trade.trailing_stop_price, candidate_stop)
        )
    return _OpenTrade(
        trade_id=open_trade.trade_id,
        symbol=open_trade.symbol,
        side=open_trade.side,
        quantity=open_trade.quantity,
        entry_timestamp_utc=open_trade.entry_timestamp_utc,
        entry_price=open_trade.entry_price,
        stop_price=open_trade.stop_price,
        max_favorable_excursion_points=open_trade.max_favorable_excursion_points,
        max_adverse_excursion_points=open_trade.max_adverse_excursion_points,
        trailing_stop_price=trailing_stop_price,
        exit_conversion_name="mfe_trailing",
        metadata=dict(open_trade.metadata),
    )


def _bar_mfe_points(side: str, entry_price: float, bar: MarketBar) -> float:
    if side == "long":
        return max(0.0, bar.high - entry_price)
    return max(0.0, entry_price - bar.low)


def _bar_mae_points(side: str, entry_price: float, bar: MarketBar) -> float:
    if side == "long":
        return min(0.0, bar.low - entry_price)
    return min(0.0, entry_price - bar.high)


def _close_trade(
    open_trade: _OpenTrade,
    exit_timestamp_utc: str,
    exit_price: float,
    exit_reason: str,
    costs: SimulationCosts,
) -> TradeFill:
    adjusted_exit_price = (
        exit_price - costs.slippage_points_per_side
        if open_trade.side == "long"
        else exit_price + costs.slippage_points_per_side
    )
    pnl_points = (
        adjusted_exit_price - open_trade.entry_price
        if open_trade.side == "long"
        else open_trade.entry_price - adjusted_exit_price
    )
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
        trailing_stop_price=open_trade.trailing_stop_price,
        exit_conversion_name=open_trade.exit_conversion_name,
        metadata=dict(open_trade.metadata),
    )
