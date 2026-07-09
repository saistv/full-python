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
        self.oco = []
        self.liquidations = []
        self.next_order_place_response = {"orderId": 101}

    def order_place(self, body):
        self.placed.append(body)
        return self.next_order_place_response

    def order_place_oco(self, body):
        self.oco.append(body)
        return {"orderId": 202}

    def order_liquidate_position(self, body):
        self.liquidations.append(body)
        return {"orderId": 303}


def _cfg(order_enabled=False, flatten_enabled=False):
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT,
        account_spec="DEMO123",
        account_id=456,
        root_symbol="NQ",
        order_enabled=order_enabled,
        flatten_enabled=flatten_enabled,
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


def test_orders_disabled_rejects_order_intent_without_calling_rest():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=False), rest)
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    assert broker.poll_events() == [Rejected(order_id="", reason="order_disabled")]
    assert rest.placed == []


def test_orders_enabled_places_automated_market_order_and_emits_ack():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True), rest)
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
    broker = TradovateBroker(_cfg(order_enabled=True), rest)
    bar = _bar()

    with pytest.raises(TradovateOrderSafetyError, match="stop_price"):
        broker.apply_strategy_result(bar, _session(bar), _entry_result(bar, metadata={}))

    assert rest.placed == []


def test_fill_raw_event_updates_position_and_emits_filled():
    broker = TradovateBroker(_cfg(), FakeRestClient())

    broker.ingest_raw_event(TradovateRawEvent(
        kind="fill",
        data={
            "orderId": 77,
            "action": "Buy",
            "qty": 1,
            "price": 100.25,
            "timestamp": "2026-07-07T14:32:00Z",
            "reason": "adaptive_trend",
        },
    ))

    assert broker.position == BrokerPosition(side="long", quantity=1, entry_price=100.25)
    assert broker.poll_events() == [Filled(
        order_id="77",
        side="buy",
        quantity=1,
        price=100.25,
        timestamp_utc="2026-07-07T14:32:00Z",
        reason="adaptive_trend",
    )]


def test_partial_fill_raw_event_maps_to_partial_filled():
    broker = TradovateBroker(_cfg(), FakeRestClient())

    broker.ingest_raw_event(TradovateRawEvent(
        kind="partial_fill",
        data={
            "orderId": 77,
            "action": "Sell",
            "qty": 1,
            "remaining": 2,
            "price": 100.25,
            "timestamp": "2026-07-07T14:32:00Z",
        },
    ))

    assert broker.poll_events() == [PartialFilled(
        order_id="77",
        side="sell",
        quantity=1,
        remaining=2,
        price=100.25,
        timestamp_utc="2026-07-07T14:32:00Z",
    )]


def test_flatten_disabled_raises_and_does_not_call_liquidation():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(flatten_enabled=False), rest)
    broker.ingest_raw_event(TradovateRawEvent(
        kind="position",
        data={"side": "long", "qty": 1, "entryPrice": 100.25},
    ))

    with pytest.raises(TradovateOrderSafetyError, match="flatten_disabled"):
        broker.flatten(_bar(), "supervisor_halt")

    assert rest.liquidations == []


def test_position_raw_event_accepts_plan_price_key():
    broker = TradovateBroker(_cfg(), FakeRestClient())

    broker.ingest_raw_event(TradovateRawEvent(
        kind="position",
        data={"side": "short", "qty": 2, "price": 101.5},
    ))

    assert broker.position == BrokerPosition(side="short", quantity=2, entry_price=101.5)


def test_flatten_enabled_with_position_calls_liquidate_position():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(flatten_enabled=True), rest)
    broker.ingest_raw_event(TradovateRawEvent(
        kind="position",
        data={"side": "long", "qty": 1, "entryPrice": 100.25},
    ))

    broker.flatten(_bar(), "supervisor_halt")

    assert rest.liquidations == [{
        "accountSpec": "DEMO123",
        "accountId": 456,
        "symbol": "NQ",
        "admin": False,
    }]


def test_partial_fill_event_from_broker_is_fatal_for_order_state_machine():
    broker = TradovateBroker(_cfg(), FakeRestClient())
    broker.ingest_raw_event(TradovateRawEvent(
        kind="partial_fill",
        data={
            "orderId": 77,
            "action": "Buy",
            "qty": 1,
            "remaining": 1,
            "price": 100.25,
            "timestamp": "2026-07-07T14:32:00Z",
        },
    ))

    sm = OrderStateMachine()
    with pytest.raises(ExecutionInvariantError, match="partial fill not modeled"):
        sm.on_event(broker.poll_events()[0])
