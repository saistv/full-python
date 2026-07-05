"""Paper broker: the Broker protocol realized over the shared
PositionEngine (identity by shared code -- design spec Amendment 2).

Fill semantics are therefore EXACTLY SimulationEngine's frozen policy:
entries at next bar open +/- slippage, stops/targets intrabar, the six
exit paths, hooks and ledger events -- because they are the same code.
This module only adds BrokerEvent synthesis from the ledger tail so the
OrderStateMachine can shadow position as an independent cross-check.
"""
from __future__ import annotations

from typing import Optional

from full_python.data.sessions import SessionInfo
from full_python.events import EventLedger, EventType
from full_python.execution.broker_protocol import (
    Acked,
    BrokerEvent,
    BrokerPosition,
    Filled,
)
from full_python.models import MarketBar, StrategyResult, Trade
from full_python.simulation.config import SimulationConfig
from full_python.simulation.position_engine import PositionEngine


class PaperBroker:
    def __init__(self, config: SimulationConfig, strategy, ledger: EventLedger) -> None:
        self._engine = PositionEngine(config, strategy, ledger)
        self._ledger = ledger
        self._ledger_cursor = len(ledger.records)
        self._intent_counter = 0
        self._exit_counter = 0
        self._open_entry_order_id: Optional[str] = None

    # -- Broker protocol -------------------------------------------------
    def process_bar_open(self, bar: MarketBar, session: SessionInfo) -> float:
        return self._engine.process_pre_strategy(bar, session)

    def apply_strategy_result(
        self, bar: MarketBar, session: SessionInfo, result: StrategyResult
    ) -> None:
        self._engine.apply_strategy_result(bar, session, result)

    def note_bar_processed(self, bar: MarketBar, session: SessionInfo) -> None:
        self._engine.note_bar_processed(bar, session)

    def close_end_of_data(self) -> None:
        self._engine.close_end_of_data()

    def flatten(self, bar: MarketBar, reason: str) -> None:
        self._engine.flatten_now(bar, reason)

    def poll_events(self) -> list[BrokerEvent]:
        events: list[BrokerEvent] = []
        records = self._ledger.records
        for record in records[self._ledger_cursor:]:
            if record.event_type == EventType.ORDER_INTENT:
                self._intent_counter += 1
                order_id = f"P{self._intent_counter}"
                self._open_entry_order_id = order_id
                events.append(Acked(order_id=order_id))
            elif record.event_type == EventType.FILL:
                payload = record.payload
                if self._open_entry_order_id is not None:
                    order_id = self._open_entry_order_id
                    self._open_entry_order_id = None
                else:
                    self._exit_counter += 1
                    order_id = f"X{self._exit_counter}"
                events.append(Filled(
                    order_id=order_id,
                    side=payload["side"],
                    quantity=payload["quantity"],
                    price=payload["price"],
                    timestamp_utc=record.timestamp_utc,
                    reason=payload["reason"],
                ))
        self._ledger_cursor = len(records)
        return events

    @property
    def position(self) -> Optional[BrokerPosition]:
        raw = self._engine.position
        if raw is None:
            return None
        return BrokerPosition(
            side=raw.side, quantity=raw.quantity, entry_price=raw.entry_price
        )

    @property
    def trades(self) -> list[Trade]:
        return self._engine.trades

    @property
    def daily_limit_hit(self) -> bool:
        return self._engine.daily_limit_hit
