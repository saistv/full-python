"""Order/position state machine for the live execution stack.

Pure, no I/O. In paper mode it SHADOWS the PositionEngine's position as
an independent cross-check (LiveLoop asserts they agree every bar); for
a real broker adapter it becomes position truth. Invariants raise
ExecutionInvariantError -- in live code that means flatten-and-halt,
never continue on a guess.

The modeled position universe is deliberately the strategy's own: one
position at a time, opened by one full fill, closed by one full fill.
Pyramiding, partial closes, and partial fills are invariant violations
until a broker adapter defines real semantics for them.
"""
from __future__ import annotations

from typing import Optional

from full_python.execution.broker_protocol import (
    Acked,
    BrokerEvent,
    BrokerPosition,
    Canceled,
    Filled,
    PartialFilled,
    Rejected,
)


class ExecutionInvariantError(RuntimeError):
    pass


class OrderStateMachine:
    def __init__(self) -> None:
        self._position: Optional[BrokerPosition] = None
        self._used_order_ids: set[str] = set()

    @property
    def position(self) -> Optional[BrokerPosition]:
        return self._position

    def on_event(self, event: BrokerEvent) -> None:
        if isinstance(event, (Acked, Rejected, Canceled)):
            return  # lifecycle notices; position only moves on fills
        if isinstance(event, PartialFilled):
            raise ExecutionInvariantError(
                f"partial fill not modeled: order {event.order_id} "
                f"filled {event.quantity} remaining {event.remaining}"
            )
        if isinstance(event, Filled):
            self._on_filled(event)
            return
        raise ExecutionInvariantError(f"unknown broker event: {event!r}")

    def _on_filled(self, fill: Filled) -> None:
        if fill.order_id in self._used_order_ids:
            raise ExecutionInvariantError(f"duplicate fill for order {fill.order_id}")
        self._used_order_ids.add(fill.order_id)

        if self._position is None:
            side = "long" if fill.side == "buy" else "short"
            self._position = BrokerPosition(
                side=side, quantity=fill.quantity, entry_price=fill.price
            )
            return

        closing_side = "sell" if self._position.side == "long" else "buy"
        if fill.side != closing_side:
            raise ExecutionInvariantError(
                f"entry fill while {self._position.side} position open "
                f"(order {fill.order_id})"
            )
        if fill.quantity != self._position.quantity:
            raise ExecutionInvariantError(
                f"exit quantity {fill.quantity} != position quantity "
                f"{self._position.quantity} (order {fill.order_id})"
            )
        self._position = None
