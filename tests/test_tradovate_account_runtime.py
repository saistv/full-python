from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from full_python.tradovate.account_runtime import (
    AccountEntityCache,
    AccountRuntimeConnection,
    AccountRuntimeState,
    TradovateAccountSyncRuntime,
)
from full_python.tradovate.auth import TradovateToken
from full_python.tradovate.config import DEMO_ENVIRONMENT, TradovateAdapterConfig
from full_python.tradovate.errors import TradovateStateError


def _collections():
    return {
        "accounts": ({"id": 456, "name": "DEMO123"},),
        "contracts": ({"id": 789, "name": "NQU6"},),
        "positions": (),
        "orders": (),
        "commands": (),
        "commandReports": (),
        "orderVersions": (),
        "fills": (),
        "cashBalances": ({
            "id": 900,
            "accountId": 456,
            "realizedPnL": -25.0,
        },),
        "accountRiskStatuses": ({"id": 456, "adminAction": "Normal"},),
    }


def _event(entity_type, event_type, entity):
    return {
        "e": "props",
        "d": {
            "entityType": entity_type,
            "eventType": event_type,
            "entity": entity,
        },
    }


def test_cache_applies_create_update_and_delete_by_strict_entity_id():
    cache = AccountEntityCache.from_collections(_collections())
    created = {
        "id": 701,
        "accountId": 456,
        "contractId": 789,
        "netPos": 1,
        "netPrice": 20100.25,
    }

    cache.apply_property_event(_event("position", "Created", created))
    cache.apply_property_event(_event("position", "Updated", {
        "id": 701,
        "netPos": 2,
    }))

    assert cache.collections()["positions"] == ({
        **created,
        "netPos": 2,
    },)

    cache.apply_property_event(_event("position", "Deleted", {"id": 701}))
    assert cache.collections()["positions"] == ()


def test_identical_create_and_update_replays_are_idempotent():
    cache = AccountEntityCache.from_collections(_collections())
    order = {"id": 801, "accountId": 456, "ordStatus": "Working"}

    cache.apply_property_event(_event("order", "Created", order))
    cache.apply_property_event(_event("order", "Created", deepcopy(order)))
    cache.apply_property_event(_event("order", "Updated", {"id": 801}))
    cache.apply_property_event(_event("order", "Updated", {"id": 801}))

    assert cache.collections()["orders"] == (order,)


def test_conflicting_create_fails_closed_without_mutating_cache():
    cache = AccountEntityCache.from_collections(_collections())
    order = {"id": 801, "accountId": 456, "ordStatus": "Working"}
    cache.apply_property_event(_event("order", "Created", order))
    before = cache.collections()

    with pytest.raises(TradovateStateError, match="conflicting Created"):
        cache.apply_property_event(_event("order", "Created", {
            **order,
            "ordStatus": "Filled",
        }))

    assert cache.collections() == before


def test_unknown_update_or_delete_is_treated_as_a_history_gap():
    cache = AccountEntityCache.from_collections(_collections())

    with pytest.raises(TradovateStateError, match="unknown entity id 801"):
        cache.apply_property_event(_event("order", "Updated", {"id": 801}))
    with pytest.raises(TradovateStateError, match="unknown entity id 801"):
        cache.apply_property_event(_event("order", "Deleted", {"id": 801}))


def test_array_event_is_atomic_when_a_later_entity_is_invalid():
    cache = AccountEntityCache.from_collections(_collections())
    before = cache.collections()

    with pytest.raises(TradovateStateError, match="invalid id"):
        cache.apply_property_event(_event("order", "Created", [
            {"id": 801, "accountId": 456},
            {"id": True, "accountId": 456},
        ]))

    assert cache.collections() == before


@pytest.mark.parametrize(
    "event",
    [
        {"e": "chart", "d": {}},
        _event("mystery", "Created", {"id": 1}),
        _event("order", "Mystery", {"id": 1}),
        _event("order", "Created", "not-an-entity"),
        {"e": "props", "d": []},
    ],
)
def test_unknown_or_malformed_property_events_fail_closed(event):
    cache = AccountEntityCache.from_collections(_collections())

    with pytest.raises(TradovateStateError):
        cache.apply_property_event(event)


def test_initial_cache_rejects_missing_collection_and_duplicate_ids():
    missing = _collections()
    del missing["orders"]
    with pytest.raises(TradovateStateError, match="missing required orders"):
        AccountEntityCache.from_collections(missing)

    duplicate = _collections()
    duplicate["orders"] = ({"id": 801}, {"id": 801})
    with pytest.raises(TradovateStateError, match="duplicate orders"):
        AccountEntityCache.from_collections(duplicate)


def _config():
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT,
        account_spec="DEMO123",
        account_id=456,
        root_symbol="NQ",
        contract_symbol="NQU6",
        contract_id=789,
        order_enabled=True,
        flatten_enabled=True,
        dollar_point_value=20.0,
        daily_loss_limit=1000.0,
    )


def _complete_collections(**overrides):
    values = {
        "accounts": [{
            "id": 456, "name": "DEMO123", "closed": False,
            "readonly": False, "futuresDisabled": False,
        }],
        "contracts": [{"id": 789, "name": "NQU6"}],
        "positions": [],
        "orders": [],
        "commands": [],
        "commandReports": [],
        "orderVersions": [],
        "fills": [],
        "cashBalances": [{
            "id": 900,
            "accountId": 456,
            "tradeDate": {"year": 2026, "month": 7, "day": 15},
            "realizedPnL": -25.0,
        }],
        "accountRiskStatuses": [{
            "id": 456, "adminAction": "Normal", "liquidateOnly": False,
            "userTriggeredLiqOnly": False,
        }],
    }
    values.update(overrides)
    return values


class FakeRest:
    def __init__(self, values, trace=None):
        self.values = values
        self.trace = trace if trace is not None else []

    def _get(self, name):
        self.trace.append(f"rest:{name}")
        return deepcopy(self.values[name])

    def account_list(self): return self._get("accounts")
    def position_list(self): return self._get("positions")
    def order_list(self): return self._get("orders")
    def command_list(self): return self._get("commands")
    def command_report_list(self): return self._get("commandReports")
    def order_version_list(self): return self._get("orderVersions")
    def fill_list(self): return self._get("fills")
    def cash_balance_list(self): return self._get("cashBalances")
    def account_risk_status_list(self): return self._get("accountRiskStatuses")

    def contract_find(self, name):
        self.trace.append("rest:contract")
        return next(
            (deepcopy(row) for row in self.values["contracts"] if row["name"] == name),
            None,
        )


class FakeWebSocket:
    def __init__(self, initial, events=None, activity=None):
        self.initial = deepcopy(initial)
        self.events = list(events or [])
        self.last_transport_activity = activity
        self.authorized = []
        self.requests = []
        self.heartbeats = 0
        self.receive_timeouts = []
        self.closed = False

    def authorize(self, token): self.authorized.append(token)

    def request(self, endpoint, payload):
        self.requests.append((endpoint, payload))
        return deepcopy(self.initial)

    def send_heartbeat(self): self.heartbeats += 1

    def receive_event(self, timeout_seconds):
        self.receive_timeouts.append(timeout_seconds)
        if not self.events:
            return None
        return self.events.pop(0)

    def close(self): self.closed = True


class FakeBroker:
    def __init__(self, trace=None):
        self.trace = trace if trace is not None else []
        self.snapshots = []

    def invalidate_account_state(self, reason):
        self.trace.append(f"invalidate:{reason}")

    def hydrate_account_state(self, snapshot):
        self.trace.append("hydrate")
        self.snapshots.append(snapshot)


class ManualClock:
    def __init__(self, value=0.0): self.value = value
    def __call__(self): return self.value


class FakeAuth:
    def __init__(self, renewed=None, error=None):
        self.renewed = renewed
        self.error = error
        self.calls = []

    def renew_access_token(self, token):
        self.calls.append(token)
        if self.error is not None:
            raise self.error
        return self.renewed


def _token(value="token-1", user_id=42, minutes=60):
    return TradovateToken(
        access_token=value,
        md_access_token=f"md-{value}",
        user_id=user_id,
        expiration_time=datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc)
        + timedelta(minutes=minutes),
    )


def _runtime(
    connections,
    *,
    broker=None,
    auth=None,
    token=None,
    clock=None,
    now=None,
    reconciliation_interval_seconds=30.0,
):
    made = []

    def factory(current_token):
        made.append(current_token)
        if not connections:
            raise AssertionError("no scripted connection")
        return connections.pop(0)

    clock = clock or ManualClock()
    current_now = now or datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc)
    runtime = TradovateAccountSyncRuntime(
        _config(),
        broker=broker or FakeBroker(),
        auth_client=auth or FakeAuth(),
        token=token or _token(),
        expected_trade_date=date(2026, 7, 15),
        connection_factory=factory,
        monotonic_clock=clock,
        utc_clock=lambda: current_now,
        reconciliation_interval_seconds=reconciliation_interval_seconds,
    )
    return runtime, made


def test_runtime_starts_closed_and_opens_only_after_full_sync_and_rest_agree():
    values = _complete_collections()
    trace = []
    ws = FakeWebSocket(values)
    rest = FakeRest(values, trace)
    broker = FakeBroker(trace)
    runtime, made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=rest)],
        broker=broker,
    )

    assert runtime.state == AccountRuntimeState.DISCONNECTED
    runtime.start()

    assert runtime.state == AccountRuntimeState.SYNCHRONIZED
    assert ws.authorized == ["token-1"]
    assert made == [_token()]
    assert trace[0].startswith("invalidate:")
    assert trace[-1] == "hydrate"
    assert broker.snapshots[-1].entry_permitted is True


def test_property_update_invalidates_before_rest_and_reopens_after_agreement():
    values = _complete_collections()
    trace = []
    ws = FakeWebSocket(values, events=[_event(
        "cashBalance",
        "Updated",
        {"id": 900, "realizedPnL": -30.0},
    )])
    rest = FakeRest(values, trace)
    broker = FakeBroker(trace)
    runtime, _made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=rest)],
        broker=broker,
    )
    runtime.start()
    rest.values["cashBalances"][0]["realizedPnL"] = -30.0
    trace.clear()

    runtime.run_once(max_wait_seconds=1.0)

    assert trace[0] == "invalidate:user sync property update"
    assert trace[1].startswith("rest:")
    assert trace[-1] == "hydrate"
    assert runtime.state == AccountRuntimeState.SYNCHRONIZED
    assert broker.snapshots[-1].daily_realized_pnl == -30.0


@pytest.mark.parametrize("event", [
    {"e": "shutdown", "d": {"reasonCode": "Maintenance"}},
    {"e": "props", "d": {"entityType": "order", "eventType": "Updated",
                           "entity": {"id": 999}}},
    {"e": "mystery", "d": {}},
])
def test_shutdown_gap_or_unknown_event_invalidates_closes_and_raises(event):
    values = _complete_collections()
    ws = FakeWebSocket(values, events=[event])
    broker = FakeBroker()
    runtime, _made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=FakeRest(values))],
        broker=broker,
    )
    runtime.start()

    with pytest.raises(TradovateStateError):
        runtime.run_once(max_wait_seconds=1.0)

    assert runtime.state == AccountRuntimeState.RECOVERY_REQUIRED
    assert ws.closed is True
    assert broker.trace[-1].startswith("invalidate:")


def test_heartbeat_deadline_caps_receive_wait_and_stale_connection_fails_closed():
    values = _complete_collections()
    clock = ManualClock()
    ws = FakeWebSocket(values)
    runtime, _made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=FakeRest(values))],
        clock=clock,
    )
    runtime.start()
    clock.value = 2.5

    runtime.run_once(max_wait_seconds=20.0)

    assert ws.heartbeats == 1
    assert ws.receive_timeouts == [2.5]

    clock.value = 8.0
    with pytest.raises(TradovateStateError, match="liveness"):
        runtime.run_once(max_wait_seconds=0.0)
    assert ws.closed is True


@pytest.mark.parametrize("activity", [float("nan"), float("inf"), 2.0])
def test_invalid_or_future_transport_activity_fails_liveness_closed(activity):
    values = _complete_collections()
    clock = ManualClock()
    ws = FakeWebSocket(values)
    runtime, _made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=FakeRest(values))],
        clock=clock,
    )
    runtime.start()
    clock.value = 1.0
    ws.last_transport_activity = activity

    with pytest.raises(TradovateStateError, match="activity timestamp"):
        runtime.run_once(max_wait_seconds=0.0)

    assert runtime.state == AccountRuntimeState.RECOVERY_REQUIRED
    assert ws.closed is True


def test_periodic_rest_disagreement_invalidates_and_closes():
    values = _complete_collections()
    rest = FakeRest(values)
    ws = FakeWebSocket(values)
    clock = ManualClock()
    runtime, _made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=rest)],
        clock=clock,
    )
    runtime.start()
    rest.values["positions"] = [{
        "id": 701, "accountId": 456, "contractId": 789,
        "netPos": 1, "netPrice": 20100.25,
    }]
    clock.value = 30.0
    ws.last_transport_activity = 30.0

    with pytest.raises(TradovateStateError, match="positions disagree"):
        runtime.run_once(max_wait_seconds=0.0)

    assert runtime.state == AccountRuntimeState.RECOVERY_REQUIRED
    assert ws.closed is True


def test_due_token_renewal_replaces_both_clients_and_fully_rehydrates():
    values = _complete_collections()
    ws1 = FakeWebSocket(values)
    ws2 = FakeWebSocket(values)
    new_token = _token("token-2", minutes=120)
    auth = FakeAuth(renewed=new_token)
    broker = FakeBroker()
    runtime, made = _runtime(
        [
            AccountRuntimeConnection(websocket=ws1, rest_client=FakeRest(values)),
            AccountRuntimeConnection(websocket=ws2, rest_client=FakeRest(values)),
        ],
        broker=broker,
        auth=auth,
        token=_token(minutes=0),
    )
    runtime.start()

    runtime.run_once(max_wait_seconds=0.0)

    assert ws1.closed is True
    assert ws2.authorized == ["token-2"]
    assert made[-1] == new_token
    assert len(broker.snapshots) == 2
    assert runtime.state == AccountRuntimeState.SYNCHRONIZED


def test_token_identity_change_fails_closed_without_building_new_clients():
    values = _complete_collections()
    ws = FakeWebSocket(values)
    auth = FakeAuth(renewed=_token("token-2", user_id=99, minutes=120))
    runtime, made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=FakeRest(values))],
        auth=auth,
        token=_token(minutes=0),
    )
    runtime.start()

    with pytest.raises(TradovateStateError, match="user identity"):
        runtime.run_once(max_wait_seconds=0.0)

    assert len(made) == 1
    assert ws.closed is True
    assert runtime.state == AccountRuntimeState.RECOVERY_REQUIRED


def test_token_renewal_failure_keeps_old_connection_closed():
    values = _complete_collections()
    ws = FakeWebSocket(values)
    auth = FakeAuth(error=RuntimeError("renewal unavailable"))
    runtime, made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=FakeRest(values))],
        auth=auth,
        token=_token(minutes=0),
    )
    runtime.start()

    with pytest.raises(RuntimeError, match="renewal unavailable"):
        runtime.run_once(max_wait_seconds=0.0)

    assert len(made) == 1
    assert ws.closed is True
    assert runtime.state == AccountRuntimeState.RECOVERY_REQUIRED


def test_authorization_failure_closes_new_connection_and_never_hydrates():
    class FailingWebSocket(FakeWebSocket):
        def authorize(self, token):
            raise RuntimeError("authorization denied")

    values = _complete_collections()
    ws = FailingWebSocket(values)
    broker = FakeBroker()
    runtime, _made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=FakeRest(values))],
        broker=broker,
    )

    with pytest.raises(RuntimeError, match="authorization denied"):
        runtime.start()

    assert ws.closed is True
    assert broker.snapshots == []
    assert runtime.state == AccountRuntimeState.RECOVERY_REQUIRED


def test_explicit_restart_uses_a_fresh_connection_after_failure():
    values = _complete_collections()
    ws1 = FakeWebSocket(values, events=[{"e": "shutdown", "d": {}}])
    ws2 = FakeWebSocket(values)
    runtime, made = _runtime([
        AccountRuntimeConnection(websocket=ws1, rest_client=FakeRest(values)),
        AccountRuntimeConnection(websocket=ws2, rest_client=FakeRest(values)),
    ])
    runtime.start()
    with pytest.raises(TradovateStateError):
        runtime.run_once(max_wait_seconds=0.0)

    runtime.start()

    assert len(made) == 2
    assert ws2.authorized == ["token-1"]
    assert runtime.state == AccountRuntimeState.SYNCHRONIZED


def test_close_disconnects_and_invalidates_state():
    values = _complete_collections()
    ws = FakeWebSocket(values)
    broker = FakeBroker()
    runtime, _made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=FakeRest(values))],
        broker=broker,
    )
    runtime.start()

    runtime.close()

    assert ws.closed is True
    assert runtime.state == AccountRuntimeState.DISCONNECTED
    assert broker.trace[-1] == "invalidate:account sync runtime closed"


def test_close_error_cannot_mask_protocol_failure_or_leave_state_synchronized():
    class CloseFailingWebSocket(FakeWebSocket):
        def close(self):
            self.closed = True
            raise RuntimeError("socket close failed")

    values = _complete_collections()
    ws = CloseFailingWebSocket(values, events=[{"e": "mystery", "d": {}}])
    runtime, _made = _runtime(
        [AccountRuntimeConnection(websocket=ws, rest_client=FakeRest(values))]
    )
    runtime.start()

    with pytest.raises(TradovateStateError, match="unknown Tradovate"):
        runtime.run_once(max_wait_seconds=0.0)

    assert runtime.state == AccountRuntimeState.RECOVERY_REQUIRED
