import pytest

from full_python.tradovate.errors import TradovateStateError
from full_python.tradovate.order_pump import OrderEventPump


def _props(entity_type, event_type, entity):
    return {
        "e": "props",
        "d": {"entityType": entity_type, "eventType": event_type, "entity": entity},
    }


class FakeWebSocket:
    def __init__(self, events=None):
        self.events = list(events or [])
        self.heartbeats = 0
        self.receive_waits = []

    def send_heartbeat(self):
        self.heartbeats += 1

    def receive_event(self, wait_seconds):
        self.receive_waits.append(wait_seconds)
        if not self.events:
            return None
        return self.events.pop(0)


class FakeBroker:
    def __init__(self, ingest_error=None):
        self.raw_events = []
        self.rest_positions = []
        self.ingest_error = ingest_error

    def ingest_raw_event(self, raw):
        if self.ingest_error is not None:
            raise self.ingest_error
        self.raw_events.append(raw)

    def reconcile_rest_positions(self, positions):
        self.rest_positions.append(positions)


class FakeRest:
    def __init__(self, positions=None):
        self.positions = positions if positions is not None else []
        self.calls = 0

    def position_list(self):
        self.calls += 1
        return self.positions


class ManualClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


def _pump(events=None, *, broker=None, rest=None, clock=None, interval=30.0):
    ws = FakeWebSocket(events)
    broker = broker or FakeBroker()
    rest = rest or FakeRest()
    pump = OrderEventPump(
        broker=broker,
        websocket=ws,
        rest_client=rest,
        account_id=456,
        contract_id=789,
        monotonic_clock=clock or ManualClock(),
        reconciliation_interval_seconds=interval,
    )
    return pump, ws, broker, rest


def test_pump_translates_and_delivers_events_in_order():
    events = [
        _props("fill", "Created", {
            "orderId": 101, "timestamp": "t", "action": "Buy",
            "qty": 1, "price": 1.0,
        }),
        _props("order", "Updated", {"id": 102, "ordStatus": "Canceled"}),
    ]
    pump, ws, broker, _rest = _pump(events)

    delivered = pump.pump(max_wait_seconds=1.0)

    assert delivered == 2
    assert [raw.kind for raw in broker.raw_events] == ["fill", "cancel"]
    # only the first receive may block; the rest poll
    assert ws.receive_waits[0] == 1.0
    assert all(wait == 0.0 for wait in ws.receive_waits[1:])


def test_heartbeat_sent_on_cadence_not_every_pump():
    clock = ManualClock()
    pump, ws, _broker, _rest = _pump([], clock=clock)

    pump.pump()          # t=0: first heartbeat
    pump.pump()          # t=0: not due
    clock.value = 2.5
    pump.pump()          # due again

    assert ws.heartbeats == 2


def test_reconciliation_interval_triggers_rest_position_check_and_rearms():
    clock = ManualClock()
    rest = FakeRest(positions=[{"id": 1}])
    pump, _ws, broker, rest = _pump([], rest=rest, clock=clock, interval=30.0)

    pump.pump()
    assert rest.calls == 0          # not due yet

    clock.value = 30.0
    pump.pump()
    assert rest.calls == 1
    assert broker.rest_positions == [[{"id": 1}]]

    clock.value = 45.0
    pump.pump()
    assert rest.calls == 1          # re-armed to t=60, not due at t=45


def test_shutdown_frame_raises_and_delivers_nothing_after_it():
    events = [
        {"e": "shutdown", "d": {"reasonCode": "Maintenance"}},
        _props("order", "Updated", {"id": 102, "ordStatus": "Canceled"}),
    ]
    pump, _ws, broker, _rest = _pump(events)

    with pytest.raises(TradovateStateError, match="shutdown"):
        pump.pump()

    assert broker.raw_events == []


def test_broker_and_translator_errors_propagate():
    boom = TradovateStateError("duplicate fill")
    events = [_props("fill", "Created", {
        "orderId": 101, "timestamp": "t", "action": "Buy", "qty": 1, "price": 1.0,
    })]
    pump, _ws, _broker, _rest = _pump(events, broker=FakeBroker(ingest_error=boom))
    with pytest.raises(TradovateStateError, match="duplicate fill"):
        pump.pump()

    malformed = [_props("order", "Updated", {"id": 1, "ordStatus": "Mystery"})]
    pump2, _ws2, _broker2, _rest2 = _pump(malformed)
    with pytest.raises(TradovateStateError, match="unknown order status"):
        pump2.pump()


def test_non_lifecycle_props_deliver_nothing_but_pump_continues():
    events = [
        _props("cashBalance", "Updated", {"id": 900, "realizedPnL": -25.0}),
        _props("order", "Updated", {"id": 102, "ordStatus": "Canceled"}),
    ]
    pump, _ws, broker, _rest = _pump(events)

    delivered = pump.pump()

    assert delivered == 1
    assert [raw.kind for raw in broker.raw_events] == ["cancel"]


def test_constructor_and_pump_validate_numeric_arguments():
    for bad_interval in (0.0, -1.0, float("nan"), float("inf"), True):
        with pytest.raises(TradovateStateError, match="interval"):
            _pump([], interval=bad_interval)
    pump, _ws, _broker, _rest = _pump([])
    for bad_wait in (-1.0, float("nan"), float("inf"), True):
        with pytest.raises(TradovateStateError, match="max_wait_seconds"):
            pump.pump(max_wait_seconds=bad_wait)
