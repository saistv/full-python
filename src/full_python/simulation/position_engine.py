"""Position/fill lifecycle shared by SimulationEngine and (Gate 5+) the
paper broker -- identity by shared code, never by parallel
reimplementation. Behavior-preserving extraction from
simulation/engine.py (2026-07-05); the proof is the unchanged test
suite. See docs/superpowers/specs/2026-07-05-execution-core-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Optional

from full_python.data.sessions import SessionInfo
from full_python.events import EventLedger, EventType
from full_python.models import Fill, MarketBar, OrderIntent, StrategyResult, Trade
from full_python.replay import Strategy
from full_python.risk.daily_loss import is_daily_loss_breached
from full_python.risk.limits import RiskLimits
from full_python.risk.risk_manager import RiskManager
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
    stop_cancelled: bool = False  # set when a DLL flatten supersedes the stop

    @property
    def direction(self) -> int:
        return 1 if self.side == "long" else -1


@dataclass
class _PendingEntry:
    intent: OrderIntent
    stop_price: float
    target_price: Optional[float]
    remaining_delay_bars: int = 0


@dataclass
class _PendingExit:
    reason: str
    timestamp_utc: str


class PositionEngine:
    def __init__(self, config: SimulationConfig, strategy: Strategy, ledger: EventLedger) -> None:
        strategy_config = getattr(strategy, "config", None)
        if (
            getattr(strategy_config, "enable_daily_loss_limit", False)
            and getattr(strategy_config, "dollar_point_value", config.point_value)
            != config.point_value
        ):
            raise ValueError(
                "strategy dollar_point_value must match simulation point_value"
            )
        self.config = config
        self._strategy = strategy
        self._ledger = ledger
        self._risk_manager = RiskManager(
            RiskLimits(
                max_contracts=config.max_contracts,
                flatten_minutes_et=config.flatten_minutes_et,
                rth_entries_only=config.rth_entries_only,
            )
        )
        self._position: Optional[_Position] = None
        self._pending_entry: Optional[_PendingEntry] = None
        self._pending_exit: Optional[_PendingExit] = None
        self._previous_bar: Optional[MarketBar] = None
        self._previous_session: Optional[SessionInfo] = None
        self._trades: list[Trade] = []
        self._cumulative_net_pnl: float = 0.0
        self._session_start_pnl: float = 0.0
        self._daily_limit_hit: bool = False
        self._strategy_feedback: list[Fill | Trade] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def trades(self) -> list[Trade]:
        return self._trades

    @property
    def daily_limit_hit(self) -> bool:
        return self._daily_limit_hit

    @property
    def position(self) -> Optional[_Position]:
        return self._position

    @property
    def previous_bar(self) -> Optional[MarketBar]:
        return self._previous_bar

    def poll_strategy_feedback(self) -> list[Fill | Trade]:
        feedback = list(self._strategy_feedback)
        self._strategy_feedback.clear()
        return feedback

    # ------------------------------------------------------------------
    # Per-bar driver hooks
    # ------------------------------------------------------------------

    def process_pre_strategy(self, bar: MarketBar, session: SessionInfo) -> float:
        self._flatten_if_session_changed(session)
        self._process_open_gap_stop(bar)
        self._process_pending_entry(bar, session)
        self._process_pending_exit(bar)
        self._process_intrabar_stop_and_target(bar)
        self._update_excursions(bar)
        self._process_backstop_flatten(bar, session)
        return self._check_daily_loss_limit(bar)

    def apply_strategy_result(
        self, bar: MarketBar, session: SessionInfo, result: StrategyResult
    ) -> None:
        self._record_strategy_result(bar, session, result)

    def note_bar_processed(self, bar: MarketBar, session: SessionInfo) -> None:
        self._previous_bar = bar
        self._previous_session = session

    def close_end_of_data(self) -> None:
        self._close_at_end_of_data()

    # ------------------------------------------------------------------
    # Per-bar steps, in deterministic order
    # ------------------------------------------------------------------

    def _flatten_if_session_changed(self, session: SessionInfo) -> None:
        if self._previous_session is None:
            return
        if session.session_date == self._previous_session.session_date:
            return
        if self._pending_entry is not None or self._pending_exit is not None:
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=self._previous_bar.timestamp_utc,
                payload={"transition": "pending_orders_cancelled", "reason": "session_end"},
            )
            self._pending_entry = None
            self._pending_exit = None
        if self._position is not None and self._previous_bar is not None:
            self._close_position(
                raw_price=self._previous_bar.close,
                timestamp_utc=self._previous_bar.timestamp_utc,
                reason="session_end",
            )
        # New session: re-anchor the daily-loss baseline and lift the halt.
        self._session_start_pnl = self._cumulative_net_pnl
        self._daily_limit_hit = False

    def _process_open_gap_stop(self, bar: MarketBar) -> None:
        position = self._position
        if position is None or position.stop_cancelled:
            return
        gapped = (
            bar.open <= position.stop_price
            if position.side == "long"
            else bar.open >= position.stop_price
        )
        if gapped:
            self._pending_exit = None
            self._close_position(
                raw_price=bar.open,
                timestamp_utc=bar.timestamp_utc,
                reason="stop_gap",
            )

    def _process_pending_entry(self, bar: MarketBar, session: SessionInfo) -> None:
        pending = self._pending_entry
        if pending is None:
            return
        if pending.remaining_delay_bars > 0:
            pending.remaining_delay_bars -= 1
            return
        self._pending_entry = None
        if not self._entry_fill_selected(pending.intent):
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=bar.timestamp_utc,
                payload={
                    "transition": "entry_missed",
                    "symbol": pending.intent.symbol,
                    "side": pending.intent.side,
                    "intent_timestamp_utc": pending.intent.timestamp_utc,
                    "entry_fill_rate": self.config.entry_fill_rate,
                    "entry_fill_seed": self.config.entry_fill_seed,
                },
            )
            return
        self._open_position(
            intent=pending.intent,
            stop_price=pending.stop_price,
            target_price=pending.target_price,
            raw_price=bar.open,
            timestamp_utc=bar.timestamp_utc,
            session=session,
        )

    def _entry_fill_selected(self, intent: OrderIntent) -> bool:
        if self.config.entry_fill_rate >= 1.0:
            return True
        if self.config.entry_fill_rate <= 0.0:
            return False
        identity = "|".join((
            str(self.config.entry_fill_seed),
            intent.timestamp_utc,
            intent.symbol,
            intent.side,
            str(intent.quantity),
        ))
        value = int.from_bytes(
            hashlib.sha256(identity.encode("utf-8")).digest()[:8], "big"
        ) / float(1 << 64)
        return value < self.config.entry_fill_rate

    def _process_pending_exit(self, bar: MarketBar) -> None:
        pending = self._pending_exit
        if pending is None or self._position is None:
            self._pending_exit = None
            return
        self._pending_exit = None
        self._close_position(
            raw_price=bar.open,
            timestamp_utc=bar.timestamp_utc,
            reason=pending.reason,
        )

    def _update_excursions(self, bar: MarketBar) -> None:
        position = self._position
        if position is None:
            return
        if position.side == "long":
            position.mfe_points = max(position.mfe_points, bar.high - position.entry_price)
            position.mae_points = max(position.mae_points, position.entry_price - bar.low)
        else:
            position.mfe_points = max(position.mfe_points, position.entry_price - bar.low)
            position.mae_points = max(position.mae_points, bar.high - position.entry_price)

    def _process_intrabar_stop_and_target(self, bar: MarketBar) -> None:
        position = self._position
        if position is None or position.stop_cancelled:
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
            # Under the engine's stop-first policy, the stop-bar's favorable
            # extreme is not counted as achieved. It is only an OHLC upper
            # bound and may have occurred after the trade closed. Preserve the
            # previously confirmed MFE, clamp MAE to the stop, and flag the
            # trade whenever that discarded extreme could change MFE.
            if position.side == "long":
                favorable_upper = bar.high - position.entry_price
            else:
                favorable_upper = position.entry_price - bar.low
            path_ambiguous = favorable_upper > position.mfe_points
            position.mae_points = max(
                position.mae_points,
                abs(position.entry_price - position.stop_price),
            )
            self._close_position(
                raw_price=position.stop_price,
                timestamp_utc=bar.timestamp_utc,
                reason="stop",
                ambiguous=bool(target_hit or path_ambiguous),
            )
        elif target_hit:
            # A clean target fill confirms movement only as far as the target;
            # any farther same-bar high/low happened after the position may
            # already have closed.
            position.mfe_points = max(
                position.mfe_points,
                abs(float(position.target_price) - position.entry_price),
            )
            self._close_position(
                raw_price=position.target_price,
                timestamp_utc=bar.timestamp_utc,
                reason="target",
            )

    def _process_backstop_flatten(self, bar: MarketBar, session: SessionInfo) -> None:
        close_minutes = session.rth_close_minutes_et
        effective_flatten = (
            0 if close_minutes is None
            else min(self.config.flatten_minutes_et, close_minutes - 1)
        )
        if session.minutes_from_midnight_et < effective_flatten:
            return
        if session.minutes_from_midnight_et >= 18 * 60:
            return  # new CME session; handled by the session-change flatten
        if self._pending_entry is not None or self._pending_exit is not None:
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=bar.timestamp_utc,
                payload={"transition": "pending_orders_cancelled", "reason": "session_flatten"},
            )
            self._pending_entry = None
            self._pending_exit = None
        if self._position is not None:
            self._close_position(
                raw_price=bar.close,
                timestamp_utc=bar.timestamp_utc,
                reason="session_flatten",
            )

    def _check_daily_loss_limit(self, bar: MarketBar) -> float:
        """Evaluate session P&L at bar close; trigger the DLL halt on breach.

        Matches Pine: equity = realized net since session start + gross
        unrealized at the close. On breach the stop is cancelled and the
        flatten fills at the next bar's open (process_orders_on_close=false).
        Returns session P&L so the strategy context sees the same number.
        """
        unrealized = 0.0
        position = self._position
        if position is not None:
            unrealized = (
                (bar.close - position.entry_price)
                * position.direction
                * self.config.point_value
                * position.quantity
            )
        session_pnl = self._cumulative_net_pnl - self._session_start_pnl + unrealized
        if self._daily_limit_hit:
            return session_pnl
        if is_daily_loss_breached(session_pnl, self.config.daily_loss_limit):
            self._daily_limit_hit = True
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=bar.timestamp_utc,
                payload={
                    "transition": "daily_limit_hit",
                    "session_pnl": session_pnl,
                    "daily_loss_limit": self.config.daily_loss_limit,
                },
            )
            if position is not None:
                position.stop_cancelled = True
                if self._pending_exit is None:
                    self._pending_exit = _PendingExit(
                        reason="daily_limit", timestamp_utc=bar.timestamp_utc
                    )
                else:
                    self._pending_exit.reason = "daily_limit"
        return session_pnl

    def _record_strategy_result(
        self,
        bar: MarketBar,
        session: SessionInfo,
        result: StrategyResult,
    ) -> None:
        if result.signal is not None:
            self._ledger.append(
                EventType.SIGNAL_DECISION,
                timestamp_utc=result.signal.timestamp_utc,
                payload=result.signal.to_payload(),
            )
            if result.signal.decision == "rejected":
                self._ledger.append(
                    EventType.REJECTION,
                    timestamp_utc=result.signal.timestamp_utc,
                    payload=result.signal.to_payload(),
                )

        for veto in result.risk_vetoes:
            self._ledger.append(
                EventType.RISK_VETO,
                timestamp_utc=veto.timestamp_utc,
                payload=veto.to_payload(),
            )

        # Logged for audit, never applied: stops are frozen at entry.
        for stop_update in result.stop_updates:
            self._ledger.append(
                EventType.STOP_UPDATE,
                timestamp_utc=stop_update.timestamp_utc,
                payload={**stop_update.to_payload(), "applied": False},
            )

        for exit_decision in result.exits:
            if self._position is None:
                continue
            self._ledger.append(
                EventType.EXIT,
                timestamp_utc=exit_decision.timestamp_utc,
                payload=exit_decision.to_payload(),
            )
            if self.config.fill_timing == FILL_TIMING_SIGNAL_BAR_CLOSE:
                self._close_position(
                    raw_price=bar.close,
                    timestamp_utc=bar.timestamp_utc,
                    reason=exit_decision.reason,
                )
            elif self._pending_exit is None:
                self._pending_exit = _PendingExit(
                    reason=exit_decision.reason,
                    timestamp_utc=exit_decision.timestamp_utc,
                )

        for intent in result.order_intents:
            veto_reason = self._veto_reason(session, intent)
            if veto_reason is not None:
                self._ledger.append(
                    EventType.RISK_VETO,
                    timestamp_utc=intent.timestamp_utc,
                    payload={**intent.to_payload(), "veto_reason": veto_reason},
                )
                continue
            self._ledger.append(
                EventType.ORDER_INTENT,
                timestamp_utc=intent.timestamp_utc,
                payload=intent.to_payload(),
            )
            stop_price = float(intent.metadata["stop_price"])
            raw_target = intent.metadata.get("target_price")
            target_price = None if raw_target is None else float(raw_target)
            if self.config.fill_timing == FILL_TIMING_NEXT_BAR_OPEN:
                self._pending_entry = _PendingEntry(
                    intent=intent,
                    stop_price=stop_price,
                    target_price=target_price,
                    remaining_delay_bars=self.config.entry_delay_bars,
                )
            else:
                self._open_position(
                    intent=intent,
                    stop_price=stop_price,
                    target_price=target_price,
                    raw_price=bar.close,
                    timestamp_utc=bar.timestamp_utc,
                    session=session,
                )

    def _close_at_end_of_data(self) -> None:
        if self._pending_entry is not None and self._previous_bar is not None:
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=self._previous_bar.timestamp_utc,
                payload={"transition": "pending_orders_cancelled", "reason": "end_of_data"},
            )
            self._pending_entry = None
        if self._position is not None and self._previous_bar is not None:
            self._close_position(
                raw_price=self._previous_bar.close,
                timestamp_utc=self._previous_bar.timestamp_utc,
                reason="end_of_data",
            )

    # ------------------------------------------------------------------
    # Risk gate
    # ------------------------------------------------------------------

    def _veto_reason(self, session: SessionInfo, intent: OrderIntent) -> Optional[str]:
        # NOTE: reference_price is now computed eagerly here (before all veto checks),
        # whereas the original inline implementation computed it lazily (only inside the
        # final invalid_stop branches after every earlier check had passed). This is safe
        # because every current strategy (baseline.py, vwap_reversion.py, adaptive_trend.py)
        # always populates intent.metadata["signal_price"] with a numeric bar.close value,
        # so _reference_price() cannot raise or behave unexpectedly. However, if a future
        # strategy supplies non-numeric/absent signal_price and an intent that would be
        # vetoed earlier (e.g., invalid_quantity), that potential failure is now hit eagerly
        # instead of never reached. If this ever needs to change, make reference_price lazy
        # in RiskManager.veto_reason's signature (e.g., a callable) rather than precomputing
        # it at every call site.
        return self._risk_manager.veto_reason(
            has_open_order=(
                self._position is not None
                or self._pending_entry is not None
                or self._pending_exit is not None
            ),
            daily_limit_hit=self._daily_limit_hit,
            session=session,
            intent=intent,
            reference_price=self._reference_price(intent),
        )

    def _reference_price(self, intent: OrderIntent) -> float:
        signal_price = intent.metadata.get("signal_price")
        if signal_price is not None:
            return float(signal_price)
        if self._previous_bar is not None:
            return self._previous_bar.close
        return float("nan")

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------

    def _open_position(
        self,
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

        stop_is_protective = (
            stop_price < fill_price if side == "long" else stop_price > fill_price
        )
        if not stop_is_protective:
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=timestamp_utc,
                payload={
                    "transition": "entry_invalidated_at_fill",
                    "reason": "stop_not_protective_at_fill",
                    "symbol": intent.symbol,
                    "side": side,
                    "intent_timestamp_utc": intent.timestamp_utc,
                    "raw_price": raw_price,
                    "fill_price": fill_price,
                    "stop_price": stop_price,
                },
            )
            return

        fill = Fill(
            timestamp_utc=timestamp_utc,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            price=fill_price,
            reason=intent.reason,
            metadata={"raw_price": raw_price, "slippage_points": slippage},
        )
        self._ledger.append(
            EventType.FILL, timestamp_utc=timestamp_utc, payload=fill.to_payload()
        )
        self._ledger.append(
            EventType.STOP_UPDATE,
            timestamp_utc=timestamp_utc,
            payload={
                "symbol": intent.symbol,
                "stop_price": stop_price,
                "reason": "initial_stop",
                "applied": True,
            },
        )
        self._position = _Position(
            symbol=intent.symbol,
            side=side,
            quantity=intent.quantity,
            entry_timestamp_utc=timestamp_utc,
            entry_price=fill_price,
            stop_price=stop_price,
            target_price=target_price,
            session_date=session.session_date.isoformat(),
        )
        self._strategy_feedback.append(fill)

    def _close_position(
        self,
        *,
        raw_price: float,
        timestamp_utc: str,
        reason: str,
        ambiguous: bool = False,
    ) -> None:
        position = self._position
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
        self._ledger.append(
            EventType.FILL, timestamp_utc=timestamp_utc, payload=fill.to_payload()
        )

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
        self._ledger.append(
            EventType.TRADE_CLOSED, timestamp_utc=timestamp_utc, payload=trade.to_payload()
        )
        self._trades.append(trade)
        self._cumulative_net_pnl += trade.net_pnl
        self._position = None
        self._pending_exit = None
        self._strategy_feedback.append(trade)

    def flatten_now(self, bar: MarketBar, reason: str) -> None:
        """Supervisor-initiated flatten: cancel pendings, close at bar close.

        Exists only for the live-execution supervisor path
        (execution/supervisor.py). SimulationEngine never calls this --
        the deterministic replay path is unchanged.
        """
        if self._pending_entry is not None or self._pending_exit is not None:
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=bar.timestamp_utc,
                payload={"transition": "pending_orders_cancelled", "reason": reason},
            )
            self._pending_entry = None
            self._pending_exit = None
        if self._position is not None:
            self._close_position(
                raw_price=bar.close,
                timestamp_utc=bar.timestamp_utc,
                reason=reason,
            )
