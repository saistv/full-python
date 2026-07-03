"""Deterministic fill/position simulation.

Converts strategy order intents into fills, positions, and closed trades
under the policy in docs/decisions/2026-07-03-fill-simulation-policy.md:
next-bar-open fills by default, frozen stops, worst-case intrabar ordering,
costs always applied, session risk gate, and every action logged as an
event. Bar timestamps are assumed to mark the bar's OPEN time (the canonical
1-minute convention).

Strategy-issued stop updates are logged but never applied: the live
architecture uses broker-held stops frozen at entry, and the simulator
matches it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from full_python.data.sessions import SessionInfo, classify_timestamp
from full_python.events import EventLedger, EventType
from full_python.models import Fill, MarketBar, OrderIntent, StrategyResult, Trade
from full_python.replay import Strategy
from full_python.simulation.config import (
    FILL_TIMING_NEXT_BAR_OPEN,
    FILL_TIMING_SIGNAL_BAR_CLOSE,
    SimulationConfig,
)


@dataclass
class _Position:
    symbol: str
    side: str  # "long" | "short"
    quantity: int
    entry_timestamp_utc: str
    entry_price: float
    stop_price: float
    target_price: Optional[float]
    session_date: str
    mfe_points: float = 0.0
    mae_points: float = 0.0

    @property
    def direction(self) -> int:
        return 1 if self.side == "long" else -1


@dataclass
class _PendingEntry:
    intent: OrderIntent
    stop_price: float
    target_price: Optional[float]


@dataclass
class _PendingExit:
    reason: str
    timestamp_utc: str


@dataclass(frozen=True)
class SimulationResult:
    ledger: EventLedger
    trades: tuple[Trade, ...]
    session_dates: tuple[str, ...] = ()


@dataclass
class _State:
    position: Optional[_Position] = None
    pending_entry: Optional[_PendingEntry] = None
    pending_exit: Optional[_PendingExit] = None
    previous_bar: Optional[MarketBar] = None
    previous_session: Optional[SessionInfo] = None
    trades: list[Trade] = field(default_factory=list)
    strategy: Optional[Strategy] = None


class SimulationEngine:
    def __init__(self, config: SimulationConfig) -> None:
        self.config = config

    def run(
        self,
        bars: Iterable[MarketBar],
        strategy: Strategy,
        *,
        ledger: Optional[EventLedger] = None,
    ) -> SimulationResult:
        active_ledger = EventLedger() if ledger is None else ledger
        state = _State(strategy=strategy)
        session_dates: list[str] = []

        for bar in bars:
            session = classify_timestamp(bar.timestamp_utc)
            session_iso = session.session_date.isoformat()
            if not session_dates or session_dates[-1] != session_iso:
                session_dates.append(session_iso)
            active_ledger.append(
                EventType.BAR, timestamp_utc=bar.timestamp_utc, payload=bar.to_payload()
            )

            self._flatten_if_session_changed(state, session, active_ledger)
            self._process_open_gap_stop(state, bar, active_ledger)
            self._process_pending_entry(state, bar, session, active_ledger)
            self._process_pending_exit(state, bar, active_ledger)
            self._update_excursions(state, bar)
            self._process_intrabar_stop_and_target(state, bar, active_ledger)
            self._process_backstop_flatten(state, bar, session, active_ledger)

            result = strategy.on_bar(bar)
            self._record_strategy_result(state, bar, session, result, active_ledger)

            state.previous_bar = bar
            state.previous_session = session

        self._close_at_end_of_data(state, active_ledger)
        return SimulationResult(
            ledger=active_ledger,
            trades=tuple(state.trades),
            session_dates=tuple(session_dates),
        )

    # ------------------------------------------------------------------
    # Per-bar steps, in deterministic order
    # ------------------------------------------------------------------

    def _flatten_if_session_changed(
        self, state: _State, session: SessionInfo, ledger: EventLedger
    ) -> None:
        if state.previous_session is None:
            return
        if session.session_date == state.previous_session.session_date:
            return
        if state.pending_entry is not None or state.pending_exit is not None:
            ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=state.previous_bar.timestamp_utc,
                payload={"transition": "pending_orders_cancelled", "reason": "session_end"},
            )
            state.pending_entry = None
            state.pending_exit = None
        if state.position is not None and state.previous_bar is not None:
            self._close_position(
                state,
                ledger,
                raw_price=state.previous_bar.close,
                timestamp_utc=state.previous_bar.timestamp_utc,
                reason="session_end",
            )

    def _process_open_gap_stop(
        self, state: _State, bar: MarketBar, ledger: EventLedger
    ) -> None:
        position = state.position
        if position is None:
            return
        gapped = (
            bar.open <= position.stop_price
            if position.side == "long"
            else bar.open >= position.stop_price
        )
        if gapped:
            state.pending_exit = None
            self._close_position(
                state,
                ledger,
                raw_price=bar.open,
                timestamp_utc=bar.timestamp_utc,
                reason="stop_gap",
            )

    def _process_pending_entry(
        self, state: _State, bar: MarketBar, session: SessionInfo, ledger: EventLedger
    ) -> None:
        pending = state.pending_entry
        if pending is None:
            return
        state.pending_entry = None
        self._open_position(
            state,
            ledger,
            intent=pending.intent,
            stop_price=pending.stop_price,
            target_price=pending.target_price,
            raw_price=bar.open,
            timestamp_utc=bar.timestamp_utc,
            session=session,
        )

    def _process_pending_exit(
        self, state: _State, bar: MarketBar, ledger: EventLedger
    ) -> None:
        pending = state.pending_exit
        if pending is None or state.position is None:
            state.pending_exit = None
            return
        state.pending_exit = None
        self._close_position(
            state,
            ledger,
            raw_price=bar.open,
            timestamp_utc=bar.timestamp_utc,
            reason=pending.reason,
        )

    def _update_excursions(self, state: _State, bar: MarketBar) -> None:
        position = state.position
        if position is None:
            return
        if position.side == "long":
            position.mfe_points = max(position.mfe_points, bar.high - position.entry_price)
            position.mae_points = max(position.mae_points, position.entry_price - bar.low)
        else:
            position.mfe_points = max(position.mfe_points, position.entry_price - bar.low)
            position.mae_points = max(position.mae_points, bar.high - position.entry_price)

    def _process_intrabar_stop_and_target(
        self, state: _State, bar: MarketBar, ledger: EventLedger
    ) -> None:
        position = state.position
        if position is None:
            return
        if position.side == "long":
            stop_hit = bar.low <= position.stop_price
            target_hit = (
                position.target_price is not None and bar.high >= position.target_price
            )
        else:
            stop_hit = bar.high >= position.stop_price
            target_hit = (
                position.target_price is not None and bar.low <= position.target_price
            )

        if stop_hit:
            # Worst case wins: when both levels sit inside one bar, the stop
            # fills and the trade is flagged ambiguous.
            self._close_position(
                state,
                ledger,
                raw_price=position.stop_price,
                timestamp_utc=bar.timestamp_utc,
                reason="stop",
                ambiguous=bool(target_hit),
            )
        elif target_hit:
            self._close_position(
                state,
                ledger,
                raw_price=position.target_price,
                timestamp_utc=bar.timestamp_utc,
                reason="target",
            )

    def _process_backstop_flatten(
        self, state: _State, bar: MarketBar, session: SessionInfo, ledger: EventLedger
    ) -> None:
        if session.minutes_from_midnight_et < self.config.flatten_minutes_et:
            return
        if session.minutes_from_midnight_et >= 18 * 60:
            return  # new CME session; handled by the session-change flatten
        if state.pending_entry is not None or state.pending_exit is not None:
            ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=bar.timestamp_utc,
                payload={"transition": "pending_orders_cancelled", "reason": "session_flatten"},
            )
            state.pending_entry = None
            state.pending_exit = None
        if state.position is not None:
            self._close_position(
                state,
                ledger,
                raw_price=bar.close,
                timestamp_utc=bar.timestamp_utc,
                reason="session_flatten",
            )

    def _record_strategy_result(
        self,
        state: _State,
        bar: MarketBar,
        session: SessionInfo,
        result: StrategyResult,
        ledger: EventLedger,
    ) -> None:
        if result.signal is not None:
            ledger.append(
                EventType.SIGNAL_DECISION,
                timestamp_utc=result.signal.timestamp_utc,
                payload=result.signal.to_payload(),
            )
            if result.signal.decision == "rejected":
                ledger.append(
                    EventType.REJECTION,
                    timestamp_utc=result.signal.timestamp_utc,
                    payload=result.signal.to_payload(),
                )

        for veto in result.risk_vetoes:
            ledger.append(
                EventType.RISK_VETO,
                timestamp_utc=veto.timestamp_utc,
                payload=veto.to_payload(),
            )

        # Logged for audit, never applied: stops are frozen at entry.
        for stop_update in result.stop_updates:
            ledger.append(
                EventType.STOP_UPDATE,
                timestamp_utc=stop_update.timestamp_utc,
                payload={**stop_update.to_payload(), "applied": False},
            )

        for exit_decision in result.exits:
            if state.position is None:
                continue
            ledger.append(
                EventType.EXIT,
                timestamp_utc=exit_decision.timestamp_utc,
                payload=exit_decision.to_payload(),
            )
            if self.config.fill_timing == FILL_TIMING_SIGNAL_BAR_CLOSE:
                self._close_position(
                    state,
                    ledger,
                    raw_price=bar.close,
                    timestamp_utc=bar.timestamp_utc,
                    reason=exit_decision.reason,
                )
            elif state.pending_exit is None:
                state.pending_exit = _PendingExit(
                    reason=exit_decision.reason,
                    timestamp_utc=exit_decision.timestamp_utc,
                )

        for intent in result.order_intents:
            veto_reason = self._veto_reason(state, session, intent)
            if veto_reason is not None:
                ledger.append(
                    EventType.RISK_VETO,
                    timestamp_utc=intent.timestamp_utc,
                    payload={**intent.to_payload(), "veto_reason": veto_reason},
                )
                continue
            ledger.append(
                EventType.ORDER_INTENT,
                timestamp_utc=intent.timestamp_utc,
                payload=intent.to_payload(),
            )
            stop_price = float(intent.metadata["stop_price"])
            raw_target = intent.metadata.get("target_price")
            target_price = None if raw_target is None else float(raw_target)
            if self.config.fill_timing == FILL_TIMING_NEXT_BAR_OPEN:
                state.pending_entry = _PendingEntry(
                    intent=intent, stop_price=stop_price, target_price=target_price
                )
            else:
                self._open_position(
                    state,
                    ledger,
                    intent=intent,
                    stop_price=stop_price,
                    target_price=target_price,
                    raw_price=bar.close,
                    timestamp_utc=bar.timestamp_utc,
                    session=session,
                )

    def _close_at_end_of_data(self, state: _State, ledger: EventLedger) -> None:
        if state.pending_entry is not None and state.previous_bar is not None:
            ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=state.previous_bar.timestamp_utc,
                payload={"transition": "pending_orders_cancelled", "reason": "end_of_data"},
            )
            state.pending_entry = None
        if state.position is not None and state.previous_bar is not None:
            self._close_position(
                state,
                ledger,
                raw_price=state.previous_bar.close,
                timestamp_utc=state.previous_bar.timestamp_utc,
                reason="end_of_data",
            )

    # ------------------------------------------------------------------
    # Risk gate
    # ------------------------------------------------------------------

    def _veto_reason(
        self, state: _State, session: SessionInfo, intent: OrderIntent
    ) -> Optional[str]:
        if intent.side not in ("buy", "sell"):
            return "invalid_side"
        if intent.quantity < 1 or intent.quantity > self.config.max_contracts:
            return "invalid_quantity"
        if (
            state.position is not None
            or state.pending_entry is not None
            or state.pending_exit is not None
        ):
            return "position_already_open"
        if session.minutes_from_midnight_et >= self.config.flatten_minutes_et:
            return "after_flatten"
        if self.config.rth_entries_only and not session.is_rth:
            return "outside_rth"
        if "stop_price" not in intent.metadata:
            return "missing_stop"
        stop_price = float(intent.metadata["stop_price"])
        if intent.side == "buy" and stop_price >= self._reference_price(state, intent):
            return "invalid_stop"
        if intent.side == "sell" and stop_price <= self._reference_price(state, intent):
            return "invalid_stop"
        return None

    def _reference_price(self, state: _State, intent: OrderIntent) -> float:
        signal_price = intent.metadata.get("signal_price")
        if signal_price is not None:
            return float(signal_price)
        if state.previous_bar is not None:
            return state.previous_bar.close
        return float("nan")

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------

    def _open_position(
        self,
        state: _State,
        ledger: EventLedger,
        *,
        intent: OrderIntent,
        stop_price: float,
        target_price: Optional[float],
        raw_price: float,
        timestamp_utc: str,
        session: SessionInfo,
    ) -> None:
        side = "long" if intent.side == "buy" else "short"
        direction = 1 if side == "long" else -1
        slippage = self.config.entry_slippage_points
        if session.is_rth_open_window:
            slippage += self.config.rth_open_extra_entry_slippage_points
        fill_price = raw_price + direction * slippage

        fill = Fill(
            timestamp_utc=timestamp_utc,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            price=fill_price,
            reason=intent.reason,
            metadata={"raw_price": raw_price, "slippage_points": slippage},
        )
        ledger.append(EventType.FILL, timestamp_utc=timestamp_utc, payload=fill.to_payload())
        ledger.append(
            EventType.STOP_UPDATE,
            timestamp_utc=timestamp_utc,
            payload={
                "symbol": intent.symbol,
                "stop_price": stop_price,
                "reason": "initial_stop",
                "applied": True,
            },
        )
        state.position = _Position(
            symbol=intent.symbol,
            side=side,
            quantity=intent.quantity,
            entry_timestamp_utc=timestamp_utc,
            entry_price=fill_price,
            stop_price=stop_price,
            target_price=target_price,
            session_date=session.session_date.isoformat(),
        )
        # Strategy feedback hook: fills are how a decision-only strategy
        # learns its actual entry price (fill anchoring, cooldown state).
        on_fill = getattr(state.strategy, "on_fill", None)
        if on_fill is not None:
            on_fill(fill)

    def _close_position(
        self,
        state: _State,
        ledger: EventLedger,
        *,
        raw_price: float,
        timestamp_utc: str,
        reason: str,
        ambiguous: bool = False,
    ) -> None:
        position = state.position
        if position is None:
            return
        direction = position.direction
        fill_price = raw_price - direction * self.config.exit_slippage_points
        exit_side = "sell" if position.side == "long" else "buy"

        fill = Fill(
            timestamp_utc=timestamp_utc,
            symbol=position.symbol,
            side=exit_side,
            quantity=position.quantity,
            price=fill_price,
            reason=reason,
            ambiguous=ambiguous,
            metadata={
                "raw_price": raw_price,
                "slippage_points": self.config.exit_slippage_points,
            },
        )
        ledger.append(EventType.FILL, timestamp_utc=timestamp_utc, payload=fill.to_payload())

        gross_points = (fill_price - position.entry_price) * direction
        gross_pnl = gross_points * self.config.point_value * position.quantity
        commission = self.config.commission_per_contract_round_trip * position.quantity
        trade = Trade(
            symbol=position.symbol,
            side=position.side,
            quantity=position.quantity,
            entry_timestamp_utc=position.entry_timestamp_utc,
            entry_price=position.entry_price,
            exit_timestamp_utc=timestamp_utc,
            exit_price=fill_price,
            exit_reason=reason,
            stop_price=position.stop_price,
            gross_points=gross_points,
            gross_pnl=gross_pnl,
            commission=commission,
            net_pnl=gross_pnl - commission,
            mfe_points=position.mfe_points,
            mae_points=position.mae_points,
            session_date=position.session_date,
            ambiguous_exit=ambiguous,
        )
        ledger.append(
            EventType.TRADE_CLOSED, timestamp_utc=timestamp_utc, payload=trade.to_payload()
        )
        state.trades.append(trade)
        state.position = None
        state.pending_exit = None
        on_trade_closed = getattr(state.strategy, "on_trade_closed", None)
        if on_trade_closed is not None:
            on_trade_closed(trade)
