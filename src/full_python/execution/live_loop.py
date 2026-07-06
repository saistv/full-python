"""The live execution conductor.

Mirrors SimulationEngine.run's per-bar sequence exactly (that identity
is test-enforced trade-for-trade and ledger-sequence-for-sequence);
adds only observation and defense: OrderStateMachine shadow
cross-check, RiskSupervisor marks, and flatten-and-halt on any
invariant violation. With the supervisor disabled the additions are
pure observation -- which is the identity argument.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional, Sequence

from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger, EventType
from full_python.execution.broker_protocol import Broker
from full_python.execution.state_machine import (
    ExecutionInvariantError,
    OrderStateMachine,
)
from full_python.execution.supervisor import RiskSupervisor
from full_python.livedata.errors import LiveDataError
from full_python.models import MarketBar, Trade


class RecordedBarSource:
    def __init__(self, bars: Sequence[MarketBar]) -> None:
        self._bars = list(bars)

    def __iter__(self) -> Iterator[MarketBar]:
        return iter(self._bars)


@dataclass(frozen=True)
class LiveLoopResult:
    trades: tuple[Trade, ...]
    halted_reason: Optional[str]


class LiveLoop:
    def __init__(
        self,
        bar_source: Iterable[MarketBar],
        strategy,
        broker: Broker,
        supervisor: RiskSupervisor,
        ledger: EventLedger,
    ) -> None:
        self._bar_source = bar_source
        self._strategy = strategy
        self._broker = broker
        self._supervisor = supervisor
        self._ledger = ledger
        self._state_machine = OrderStateMachine()

    def run(self) -> LiveLoopResult:
        halted_reason: Optional[str] = None
        breach_flattened: set[str] = set()  # session_dates already acted on
        last_timestamp = ""  # for stamping a halt raised outside a live bar
        last_bar = None  # last MarketBar seen, for flattening on a data outage
        try:
            for bar in self._bar_source:
                last_timestamp = bar.timestamp_utc
                last_bar = bar
                session = classify_timestamp(bar.timestamp_utc)
                session_iso = session.session_date.isoformat()
                self._ledger.append(
                    EventType.BAR, timestamp_utc=bar.timestamp_utc, payload=bar.to_payload()
                )

                session_pnl = self._broker.process_bar_open(bar, session)
                self._drain_events()
                self._cross_check()

                breach = self._supervisor.check_mark(
                    session_date=session_iso,
                    bar=bar,
                    position=self._broker.position,
                    trades=self._broker.trades,
                )
                if breach is not None and session_iso not in breach_flattened:
                    breach_flattened.add(session_iso)
                    self._ledger.append(
                        EventType.STATE_TRANSITION,
                        timestamp_utc=bar.timestamp_utc,
                        payload={"transition": "execution_halt", "reason": breach},
                    )
                    self._broker.flatten(bar, breach)
                    self._drain_events()
                    self._cross_check()

                on_bar_context = getattr(self._strategy, "on_bar_context", None)
                if on_bar_context is not None:
                    on_bar_context(
                        session_pnl=session_pnl,
                        daily_limit_hit=self._broker.daily_limit_hit,
                    )
                result = self._strategy.on_bar(bar)
                if not self._supervisor.entries_allowed():
                    result = dataclasses.replace(result, order_intents=())
                self._broker.apply_strategy_result(bar, session, result)
                self._broker.note_bar_processed(bar, session)

            self._broker.close_end_of_data()
            self._drain_events()
        except ExecutionInvariantError as exc:
            halted_reason = f"execution_halt: {exc}"
            # Same "reason" key as the breach-halt path above, so a ledger
            # consumer filtering transition=="execution_halt" reads one field
            # for both halt variants; "error" carries the invariant detail.
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=last_timestamp,
                payload={
                    "transition": "execution_halt",
                    "reason": "invariant_violation",
                    "error": str(exc),
                },
            )
        except LiveDataError as exc:
            halted_reason = f"data_outage: {exc}"
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=last_timestamp,
                payload={
                    "transition": "execution_halt",
                    "reason": "data_outage",
                    "error": str(exc),
                },
            )
            # Unlike an invariant halt (position unknown -> do not flatten),
            # a data outage leaves the BROKER authoritative -- flatten the
            # open position at the last-seen bar before halting.
            if last_bar is not None and self._broker.position is not None:
                self._broker.flatten(last_bar, "data_outage")
        return LiveLoopResult(
            trades=tuple(self._broker.trades), halted_reason=halted_reason
        )

    def _drain_events(self) -> None:
        for event in self._broker.poll_events():
            self._state_machine.on_event(event)

    def _cross_check(self) -> None:
        shadow = self._state_machine.position
        truth = self._broker.position
        if (shadow is None) != (truth is None):
            raise ExecutionInvariantError(
                f"state-machine/broker position mismatch: shadow={shadow!r} truth={truth!r}"
            )
        if shadow is not None and truth is not None:
            if shadow.side != truth.side or shadow.quantity != truth.quantity:
                raise ExecutionInvariantError(
                    f"state-machine/broker position mismatch: shadow={shadow!r} truth={truth!r}"
                )
