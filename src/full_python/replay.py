from __future__ import annotations

from typing import Iterable, Protocol

from full_python.events import EventLedger, EventType
from full_python.models import MarketBar, StrategyResult


class Strategy(Protocol):
    def on_bar(self, bar: MarketBar) -> StrategyResult:
        ...


class ReplayEngine:
    def run(
        self,
        bars: Iterable[MarketBar],
        strategy: Strategy,
        *,
        ledger: EventLedger | None = None,
    ) -> EventLedger:
        active_ledger = EventLedger() if ledger is None else ledger

        for bar in bars:
            active_ledger.append(
                EventType.BAR,
                timestamp_utc=bar.timestamp_utc,
                payload=bar.to_payload(),
            )
            result = strategy.on_bar(bar)
            self._record_result(active_ledger, result)

        return active_ledger

    def _record_result(self, ledger: EventLedger, result: StrategyResult) -> None:
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

        for order_intent in result.order_intents:
            ledger.append(
                EventType.ORDER_INTENT,
                timestamp_utc=order_intent.timestamp_utc,
                payload=order_intent.to_payload(),
            )

        for stop_update in result.stop_updates:
            ledger.append(
                EventType.STOP_UPDATE,
                timestamp_utc=stop_update.timestamp_utc,
                payload=stop_update.to_payload(),
            )

        for exit_decision in result.exits:
            ledger.append(
                EventType.EXIT,
                timestamp_utc=exit_decision.timestamp_utc,
                payload=exit_decision.to_payload(),
            )
