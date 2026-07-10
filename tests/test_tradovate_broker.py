import pytest

from full_python.data.sessions import classify_timestamp
from full_python.execution.broker_protocol import (
    Acked,
    BrokerPosition,
    Filled,
    PartialFilled,
    Rejected,
)
from full_python.execution.state_machine import ExecutionInvariantError, OrderStateMachine
from full_python.models import MarketBar, OrderIntent, StrategyResult
from full_python.tradovate.broker import TradovateBroker, TradovateRawEvent
from full_python.tradovate.config import DEMO_ENVIRONMENT, TradovateAdapterConfig
from full_python.tradovate.errors import TradovateOrderSafetyError


class FakeRestClient:
    def __init__(self):
        self.placed = []
        self.canceled = []
        self.liquidations = []
        # queue of order_place responses; each call pops one (default ids 101, 102, ...)
        self.order_place_responses = []
        self._auto_id = 100
        self.order_place_error = None      # set to an exception to make order_place raise
        self.order_cancel_error = None     # set to an exception to make order_cancel raise

    def order_place(self, body):
        if self.order_place_error is not None:
            error, self.order_place_error = self.order_place_error, None
            raise error
        self.placed.append(body)
        if self.order_place_responses:
            return self.order_place_responses.pop(0)
        self._auto_id += 1
        return {"orderId": self._auto_id}

    def order_cancel(self, body):
        if self.order_cancel_error is not None:
            error, self.order_cancel_error = self.order_cancel_error, None
            raise error
        self.canceled.append(body)
        return {}

    def order_liquidate_position(self, body):
        self.liquidations.append(body)
        self._auto_id += 1
        return {"orderId": self._auto_id}


def _cfg(order_enabled=False, flatten_enabled=False, daily_loss_limit=1000.0):
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT,
        account_spec="DEMO123",
        account_id=456,
        root_symbol="NQ",
        order_enabled=order_enabled,
        flatten_enabled=flatten_enabled,
        dollar_point_value=20.0,
        commission_per_contract_round_trip=1.0,
        daily_loss_limit=daily_loss_limit,
    )


def _bar():
    return MarketBar(
        timestamp_utc="2026-07-07T14:32:00Z",
        symbol="NQ",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.25,
        volume=1.0,
    )


def _session(bar=None):
    return classify_timestamp((bar or _bar()).timestamp_utc)


def _entry_result(bar=None, side="buy", metadata=None):
    bar = bar or _bar()
    return StrategyResult(order_intents=(
        OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=side,
            quantity=1,
            reason="adaptive_trend",
            metadata={"stop_price": 95.0} if metadata is None else metadata,
        ),
    ))


def _fill_event(order_id, action="Buy", qty=1, price=100.25, ts="2026-07-07T14:32:00Z", reason=""):
    return TradovateRawEvent(kind="fill", data={
        "orderId": order_id, "action": action, "qty": qty,
        "price": price, "timestamp": ts, "reason": reason,
    })


def _entered_broker(rest=None, side="buy", price=100.25, config=None):
    """Broker with a filled entry: order 101 placed, filled at `price`."""
    rest = rest or FakeRestClient()
    broker = TradovateBroker(config or _cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar, side=side))
    broker.ingest_raw_event(_fill_event(101, action="Buy" if side == "buy" else "Sell", price=price))
    return broker, rest


def test_orders_disabled_rejects_order_intent_without_calling_rest():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=False), rest)
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    assert broker.poll_events() == [Rejected(order_id="", reason="order_disabled")]
    assert rest.placed == []


def test_orders_enabled_places_automated_market_order_and_emits_ack():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    assert rest.placed == [{
        "accountSpec": "DEMO123",
        "accountId": 456,
        "action": "Buy",
        "symbol": "NQ",
        "orderQty": 1,
        "orderType": "Market",
        "isAutomated": True,
    }]
    assert broker.poll_events() == [Acked(order_id="101")]


def test_live_enabled_entry_requires_stop_price_metadata():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()

    with pytest.raises(TradovateOrderSafetyError, match="stop_price"):
        broker.apply_strategy_result(bar, _session(bar), _entry_result(bar, metadata={}))

    assert rest.placed == []


def test_fill_raw_event_updates_position_and_emits_filled():
    broker, _rest = _entered_broker()

    assert broker.position == BrokerPosition(side="long", quantity=1, entry_price=100.25)
    filled = [e for e in broker.poll_events() if isinstance(e, Filled)]
    assert filled == [Filled(
        order_id="101",
        side="buy",
        quantity=1,
        price=100.25,
        timestamp_utc="2026-07-07T14:32:00Z",
        reason="",
    )]


def test_partial_fill_raw_event_maps_to_partial_filled():
    broker, _rest = _entered_broker()

    broker.ingest_raw_event(TradovateRawEvent(
        kind="partial_fill",
        data={
            "orderId": 101,
            "action": "Sell",
            "qty": 1,
            "remaining": 2,
            "price": 100.25,
            "timestamp": "2026-07-07T14:32:00Z",
        },
    ))

    partials = [e for e in broker.poll_events() if isinstance(e, PartialFilled)]
    assert partials == [PartialFilled(
        order_id="101",
        side="sell",
        quantity=1,
        remaining=2,
        price=100.25,
        timestamp_utc="2026-07-07T14:32:00Z",
    )]


def test_position_snapshot_matching_fill_derived_state_passes():
    broker, _rest = _entered_broker()

    broker.ingest_raw_event(TradovateRawEvent(
        kind="position",
        data={"side": "long", "qty": 1, "price": 100.25},
    ))  # matching snapshot: no exception

    assert broker.position == BrokerPosition(side="long", quantity=1, entry_price=100.25)


def test_flatten_disabled_raises_and_does_not_call_liquidation():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(flatten_enabled=False), rest)

    with pytest.raises(TradovateOrderSafetyError, match="flatten_disabled"):
        broker.flatten(_bar(), "supervisor_halt")

    assert rest.liquidations == []


def test_flatten_enabled_with_position_calls_liquidate_position():
    broker, rest = _entered_broker()

    broker.flatten(_bar(), "supervisor_halt")

    assert rest.liquidations == [{
        "accountSpec": "DEMO123",
        "accountId": 456,
        "symbol": "NQ",
        "admin": False,
    }]


def test_partial_fill_event_from_broker_is_fatal_for_order_state_machine():
    broker, _rest = _entered_broker()
    broker.poll_events()  # drain entry lifecycle events
    broker.ingest_raw_event(TradovateRawEvent(
        kind="partial_fill",
        data={
            "orderId": 101,
            "action": "Sell",
            "qty": 1,
            "remaining": 1,
            "price": 100.25,
            "timestamp": "2026-07-07T14:32:00Z",
        },
    ))

    sm = OrderStateMachine()
    with pytest.raises(ExecutionInvariantError, match="partial fill not modeled"):
        for event in broker.poll_events():
            sm.on_event(event)


def test_fill_for_unknown_order_id_raises_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker = TradovateBroker(_cfg(), FakeRestClient())

    with pytest.raises(TradovateStateError, match="unknown order id 999"):
        broker.ingest_raw_event(_fill_event(999))


def test_duplicate_fill_for_same_order_id_raises_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()

    with pytest.raises(TradovateStateError, match="duplicate fill"):
        broker.ingest_raw_event(_fill_event(101))


def test_entry_fill_while_position_open_raises_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))  # second entry order
    acks = [e for e in broker.poll_events() if isinstance(e, Acked)]

    with pytest.raises(TradovateStateError, match="position is already open"):
        broker.ingest_raw_event(_fill_event(int(acks[-1].order_id)))


def test_reject_and_cancel_for_unknown_order_ids_raise_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker = TradovateBroker(_cfg(), FakeRestClient())

    with pytest.raises(TradovateStateError, match="unknown order id"):
        broker.ingest_raw_event(TradovateRawEvent(kind="reject", data={"orderId": 5, "reason": "x"}))
    with pytest.raises(TradovateStateError, match="unknown order id"):
        broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 6}))


def test_broker_requires_dollar_point_value_and_live_pairing():
    from full_python.tradovate.errors import TradovateConfigError

    bare = TradovateAdapterConfig(environment=DEMO_ENVIRONMENT, account_spec="D", account_id=1)
    with pytest.raises(TradovateConfigError, match="dollar_point_value"):
        TradovateBroker(bare, FakeRestClient())

    with pytest.raises(TradovateConfigError, match="daily_loss_limit"):
        TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True, daily_loss_limit=None), FakeRestClient())

    with pytest.raises(TradovateConfigError, match="flatten_enabled"):
        TradovateBroker(_cfg(order_enabled=True, flatten_enabled=False), FakeRestClient())
