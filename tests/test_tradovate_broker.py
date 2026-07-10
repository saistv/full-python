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
            "orderId": 102,
            "action": "Sell",
            "qty": 1,
            "remaining": 2,
            "price": 100.25,
            "timestamp": "2026-07-07T14:32:00Z",
        },
    ))

    partials = [e for e in broker.poll_events() if isinstance(e, PartialFilled)]
    assert partials == [PartialFilled(
        order_id="102",
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

    assert rest.canceled == [{"orderId": 102}]
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
            "orderId": 102,
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


def test_entry_fill_submits_protective_stop_at_frozen_price():
    broker, rest = _entered_broker()

    stop_bodies = [b for b in rest.placed if b.get("orderType") == "Stop"]
    assert stop_bodies == [{
        "accountSpec": "DEMO123",
        "accountId": 456,
        "action": "Sell",           # opposite of the long entry
        "symbol": "NQ",
        "orderQty": 1,
        "orderType": "Stop",
        "stopPrice": 95.0,          # frozen at the entry intent's stop_price
        "isAutomated": True,
    }]
    acks = [e for e in broker.poll_events() if isinstance(e, Acked)]
    assert [a.order_id for a in acks] == ["101", "102"]  # entry, then stop


def test_protective_stop_rest_failure_flattens_and_raises():
    from full_python.tradovate.errors import TradovateRequestError, TradovateStateError

    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))
    rest.order_place_error = TradovateRequestError("boom")

    with pytest.raises(TradovateStateError, match="protective stop"):
        broker.ingest_raw_event(_fill_event(101))

    assert rest.liquidations != []   # emergency flatten was requested


def test_protective_stop_rejection_flattens_and_raises():
    from full_python.tradovate.errors import TradovateStateError

    broker, rest = _entered_broker()   # stop order 102 is working

    with pytest.raises(TradovateStateError, match="protective stop"):
        broker.ingest_raw_event(TradovateRawEvent(
            kind="reject", data={"orderId": 102, "reason": "risk_rules"},
        ))

    assert rest.liquidations != []


def test_reject_event_for_known_entry_emits_rejected():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    broker.ingest_raw_event(TradovateRawEvent(
        kind="reject", data={"orderId": 101, "reason": "outside_market_hours"},
    ))

    rejects = [e for e in broker.poll_events() if isinstance(e, Rejected)]
    assert rejects == [Rejected(order_id="101", reason="outside_market_hours")]
    assert broker.position is None
    assert rest.liquidations == []   # entry rejection needs no flatten


def _exit_result(bar=None, reason="atf_flip"):
    from full_python.models import ExitDecision
    bar = bar or _bar()
    return StrategyResult(exits=(
        ExitDecision(timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, reason=reason),
    ))


def test_strategy_exit_cancels_stop_then_market_closes():
    broker, rest = _entered_broker()
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _exit_result(bar))

    assert rest.canceled == [{"orderId": 102}]
    close_bodies = [b for b in rest.placed if b["orderType"] == "Market"][1:]
    assert close_bodies == [{
        "accountSpec": "DEMO123",
        "accountId": 456,
        "action": "Sell",
        "symbol": "NQ",
        "orderQty": 1,
        "orderType": "Market",
        "isAutomated": True,
    }]
    # exit fill closes the trade with the strategy's reason
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    broker.ingest_raw_event(_fill_event(103, action="Sell", price=101.25,
                                        ts="2026-07-07T14:33:00Z"))
    assert broker.position is None


def test_strategy_exit_while_flat_is_a_no_op():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _exit_result(bar))

    assert rest.canceled == [] and rest.placed == []


def test_strategy_exit_stop_cancel_failure_halts_without_close_order():
    from full_python.tradovate.errors import TradovateRequestError, TradovateStateError

    broker, rest = _entered_broker()
    rest.order_cancel_error = TradovateRequestError("cancel refused")
    bar = _bar()

    with pytest.raises(TradovateStateError, match="cancel protective stop"):
        broker.apply_strategy_result(bar, _session(bar), _exit_result(bar))

    # No market close was submitted: the stop still protects the position,
    # and two live closing orders must never coexist.
    assert [b for b in rest.placed if b["orderType"] == "Market"] == [rest.placed[0]]


def test_exit_fill_quantity_mismatch_raises_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()

    with pytest.raises(TradovateStateError, match="quantity"):
        broker.ingest_raw_event(_fill_event(102, action="Sell", qty=3))


def test_cancel_event_for_known_order_emits_canceled():
    from full_python.execution.broker_protocol import Canceled

    broker, _rest = _entered_broker()

    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))

    cancels = [e for e in broker.poll_events() if isinstance(e, Canceled)]
    assert cancels == [Canceled(order_id="102")]


def test_flatten_while_flat_is_a_no_op():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(flatten_enabled=True), rest)

    broker.flatten(_bar(), "supervisor_halt")

    assert rest.liquidations == []


def test_flatten_while_short_cancels_stop_then_liquidates():
    broker, rest = _entered_broker(side="sell")

    broker.flatten(_bar(), "daily_limit")

    assert rest.canceled == [{"orderId": 102}]
    assert len(rest.liquidations) == 1
    # the liquidation order is registered: its fill is a KNOWN id
    liq_id = 103
    broker.ingest_raw_event(_fill_event(liq_id, action="Buy", price=99.0,
                                        ts="2026-07-07T14:34:00Z"))
    assert broker.position is None
