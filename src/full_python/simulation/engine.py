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

from dataclasses import dataclass
from typing import Iterable, Optional

from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger, EventType
from full_python.execution.strategy_feedback import dispatch_strategy_feedback
from full_python.models import MarketBar, Trade
from full_python.replay import Strategy
from full_python.simulation.config import SimulationConfig
from full_python.simulation.position_engine import PositionEngine


@dataclass(frozen=True)
class SimulationResult:
    ledger: EventLedger
    trades: tuple[Trade, ...]
    session_dates: tuple[str, ...] = ()


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
        engine = PositionEngine(self.config, strategy, active_ledger)
        session_dates: list[str] = []

        for bar in bars:
            session = classify_timestamp(bar.timestamp_utc)
            session_iso = session.session_date.isoformat()
            if not session_dates or session_dates[-1] != session_iso:
                session_dates.append(session_iso)
            active_ledger.append(
                EventType.BAR, timestamp_utc=bar.timestamp_utc, payload=bar.to_payload()
            )

            session_pnl = engine.process_pre_strategy(bar, session)
            dispatch_strategy_feedback(strategy, engine.poll_strategy_feedback())

            on_bar_context = getattr(strategy, "on_bar_context", None)
            if on_bar_context is not None:
                on_bar_context(session_pnl=session_pnl, daily_limit_hit=engine.daily_limit_hit)
            result = strategy.on_bar(bar)
            engine.apply_strategy_result(bar, session, result)
            dispatch_strategy_feedback(strategy, engine.poll_strategy_feedback())

            engine.note_bar_processed(bar, session)

        engine.close_end_of_data()
        dispatch_strategy_feedback(strategy, engine.poll_strategy_feedback())
        return SimulationResult(
            ledger=active_ledger,
            trades=tuple(engine.trades),
            session_dates=tuple(session_dates),
        )
