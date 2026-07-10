"""Fill-derived trade bookkeeping for a real broker adapter.

Pure, no I/O. Pairs REAL broker fills into models.Trade so that session
realized P&L, RiskSupervisor's trades view, and the strategy's DLL all
run on broker truth instead of simulated fills. The arithmetic mirrors
PositionEngine._close_position exactly (pinned by
tests/test_tradovate_ledger.py::test_trade_matches_position_engine_for_identical_fills);
only the fill source differs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from full_python.models import Trade
from full_python.tradovate.errors import TradovateStateError


@dataclass
class _OpenLeg:
    symbol: str
    side: str  # "long" | "short"
    quantity: int
    entry_timestamp_utc: str
    entry_price: float
    stop_price: float
    session_date: str
    mfe_points: float = 0.0
    mae_points: float = 0.0


class FillPairingLedger:
    def __init__(
        self, *, dollar_point_value: float, commission_per_contract_round_trip: float
    ) -> None:
        if dollar_point_value <= 0:
            raise TradovateStateError("dollar_point_value must be positive")
        if commission_per_contract_round_trip < 0:
            raise TradovateStateError("commission_per_contract_round_trip must be >= 0")
        self._dollar_point_value = dollar_point_value
        self._commission_round_trip = commission_per_contract_round_trip
        self._open_leg: Optional[_OpenLeg] = None
        self._trades: list[Trade] = []

    @property
    def has_open_leg(self) -> bool:
        return self._open_leg is not None

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    def open_leg(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        timestamp_utc: str,
        stop_price: float,
        session_date: str,
    ) -> None:
        if self._open_leg is not None:
            raise TradovateStateError("entry fill while a leg is already open")
        if side not in ("buy", "sell"):
            raise TradovateStateError(f"unsupported fill side: {side}")
        self._open_leg = _OpenLeg(
            symbol=symbol,
            side="long" if side == "buy" else "short",
            quantity=quantity,
            entry_timestamp_utc=timestamp_utc,
            entry_price=price,
            stop_price=stop_price,
            session_date=session_date,
        )

    def mark_bar(self, *, high: float, low: float) -> None:
        leg = self._open_leg
        if leg is None:
            return
        if leg.side == "long":
            leg.mfe_points = max(leg.mfe_points, high - leg.entry_price)
            leg.mae_points = max(leg.mae_points, leg.entry_price - low)
        else:
            leg.mfe_points = max(leg.mfe_points, leg.entry_price - low)
            leg.mae_points = max(leg.mae_points, high - leg.entry_price)

    def close_leg(self, *, price: float, timestamp_utc: str, reason: str) -> Trade:
        leg = self._open_leg
        if leg is None:
            raise TradovateStateError("exit fill with no open leg")
        direction = 1 if leg.side == "long" else -1
        gross_points = (price - leg.entry_price) * direction
        gross_pnl = gross_points * self._dollar_point_value * leg.quantity
        commission = self._commission_round_trip * leg.quantity
        trade = Trade(
            symbol=leg.symbol,
            side=leg.side,
            quantity=leg.quantity,
            entry_timestamp_utc=leg.entry_timestamp_utc,
            entry_price=leg.entry_price,
            exit_timestamp_utc=timestamp_utc,
            exit_price=price,
            exit_reason=reason,
            stop_price=leg.stop_price,
            gross_points=gross_points,
            gross_pnl=gross_pnl,
            commission=commission,
            net_pnl=gross_pnl - commission,
            mfe_points=leg.mfe_points,
            mae_points=leg.mae_points,
            session_date=leg.session_date,
            ambiguous_exit=False,
        )
        self._trades.append(trade)
        self._open_leg = None
        return trade

    def realized_session_pnl(self, session_date: str) -> float:
        return sum(t.net_pnl for t in self._trades if t.session_date == session_date)
