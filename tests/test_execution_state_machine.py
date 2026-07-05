import pytest

from full_python.execution.broker_protocol import (
    Acked,
    BrokerPosition,
    Canceled,
    Filled,
    PartialFilled,
    Rejected,
)
from full_python.execution.state_machine import ExecutionInvariantError, OrderStateMachine


def _fill(order_id, side, qty, price=100.0, reason="test"):
    return Filled(order_id=order_id, side=side, quantity=qty, price=price,
                  timestamp_utc="2025-10-01T14:31:00Z", reason=reason)


def test_entry_fill_opens_position_and_exit_fill_closes_it():
    sm = OrderStateMachine()
    sm.on_event(Acked(order_id="P1"))
    sm.on_event(_fill("P1", "buy", 2, price=101.0))
    assert sm.position == BrokerPosition(side="long", quantity=2, entry_price=101.0)
    sm.on_event(_fill("X1", "sell", 2, price=105.0))  # exit fills may be unsolicited
    assert sm.position is None


def test_short_position_from_sell_entry():
    sm = OrderStateMachine()
    sm.on_event(_fill("P1", "sell", 1, price=99.0))
    assert sm.position == BrokerPosition(side="short", quantity=1, entry_price=99.0)
    sm.on_event(_fill("X1", "buy", 1))
    assert sm.position is None


def test_exit_quantity_mismatch_raises():
    sm = OrderStateMachine()
    sm.on_event(_fill("P1", "buy", 2))
    with pytest.raises(ExecutionInvariantError):
        sm.on_event(_fill("X1", "sell", 1))  # partial close is not a modeled state


def test_double_fill_of_same_order_raises():
    sm = OrderStateMachine()
    sm.on_event(_fill("P1", "buy", 1))
    sm.on_event(_fill("X1", "sell", 1))
    with pytest.raises(ExecutionInvariantError):
        sm.on_event(_fill("P1", "buy", 1))  # order ids are single-use


def test_entry_fill_while_position_open_same_direction_raises():
    sm = OrderStateMachine()
    sm.on_event(_fill("P1", "buy", 1))
    with pytest.raises(ExecutionInvariantError):
        sm.on_event(_fill("P2", "buy", 1))  # pyramiding is not a modeled state


def test_rejected_and_canceled_orders_leave_position_untouched():
    sm = OrderStateMachine()
    sm.on_event(Acked(order_id="P1"))
    sm.on_event(Rejected(order_id="P1", reason="risk"))
    sm.on_event(Canceled(order_id="P2"))
    assert sm.position is None


def test_partial_fill_is_fatal_for_now():
    sm = OrderStateMachine()
    with pytest.raises(ExecutionInvariantError):
        sm.on_event(PartialFilled(order_id="P1", side="buy", quantity=1,
                                  remaining=1, price=100.0,
                                  timestamp_utc="2025-10-01T14:31:00Z"))
