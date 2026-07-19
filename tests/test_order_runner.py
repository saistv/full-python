import pytest

from full_python.events import EventLedger
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.risk.limits import RiskLimits
from full_python.tradovate.config import DEMO_ENVIRONMENT, TradovateAdapterConfig
from full_python.tradovate.errors import TradovateStateError
from full_python.live.order_runner import (
    DEFAULT_RISK_LIMITS,
    build_gate5_config,
    build_order_session,
    require_account,
)


class FakeWebSocket:
    def __init__(self):
        self.heartbeats = 0

    def send_heartbeat(self):
        self.heartbeats += 1

    def receive_event(self, wait_seconds):
        return None


class FakeRest:
    def position_list(self):
        return []


class IdleStrategy:
    def on_bar(self, bar):
        raise AssertionError("no bars are fed in composition tests")


def _observe_config():
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT,
        account_spec="DEMO123",
        account_id=456,
        root_symbol="NQ",
        contract_symbol="NQU6",
        contract_id=789,
        order_enabled=False,
        flatten_enabled=False,
        dollar_point_value=20.0,
        daily_loss_limit=1000.0,
    )


def test_build_order_session_wires_pump_into_the_maintenance_hook():
    ws = FakeWebSocket()
    captured = {}

    def bar_source_factory(maintenance):
        captured["maintenance"] = maintenance
        return []  # LiveLoop would iterate this; composition tests never run it

    session = build_order_session(
        config=_observe_config(),
        rest_client=FakeRest(),
        user_sync_ws=ws,
        strategy=IdleStrategy(),
        supervisor=RiskSupervisor(RiskSupervisorConfig(point_value=20.0)),
        ledger=EventLedger(),
        bar_source_factory=bar_source_factory,
    )

    assert session.broker is not None and session.loop is not None
    # The maintenance hook IS the pump: invoking it drains the account stream.
    captured["maintenance"]()
    assert ws.heartbeats == 1


def test_explicit_account_selection_refuses_missing_or_mismatched(
):
    accounts = [{"id": 456, "name": "DEMO123"}, {"id": 999, "name": "OTHER"}]

    assert require_account(accounts, account_id=456, account_spec="DEMO123") == {
        "id": 456, "name": "DEMO123",
    }
    with pytest.raises(TradovateStateError, match="refusing to guess"):
        require_account(accounts, account_id=111, account_spec="DEMO123")
    with pytest.raises(TradovateStateError, match="named"):
        require_account(accounts, account_id=456, account_spec="WRONG")
    with pytest.raises(TradovateStateError, match="no Tradovate accounts"):
        require_account([], account_id=456, account_spec="DEMO123")


def test_gate5_config_pins_order_and_flatten_off():
    config = build_gate5_config(
        account_id=456,
        account_spec="DEMO123",
        contract_symbol="NQU6",
        contract_id=789,
        dollar_point_value=20.0,
        daily_loss_limit=1000.0,
    )
    assert config.order_enabled is False
    assert config.flatten_enabled is False
    assert config.environment is DEMO_ENVIRONMENT


def test_default_risk_limits_match_production_envelope():
    assert DEFAULT_RISK_LIMITS == RiskLimits(
        max_contracts=1, flatten_minutes_et=959, rth_entries_only=True
    )


class FakeFlattenBroker:
    def __init__(self, resolve_after_pumps):
        self.remaining = resolve_after_pumps
        self.flatten_in_progress = True
        self.drained = 0

    def poll_events(self):
        self.drained += 1
        return []


class CountingPump:
    def __init__(self, broker, clock=None, advance=0.0):
        self.broker = broker
        self.calls = 0
        self.clock = clock
        self.advance = advance

    def pump(self, max_wait_seconds=0.0):
        self.calls += 1
        if self.clock is not None:
            self.clock.value += self.advance
        self.broker.remaining -= 1
        if self.broker.remaining <= 0:
            self.broker.flatten_in_progress = False
        return 0


class ManualClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


def test_run_startup_flatten_pumps_until_resolved():
    from full_python.live.order_runner import run_startup_flatten

    broker = FakeFlattenBroker(resolve_after_pumps=3)
    pump = CountingPump(broker)

    run_startup_flatten(
        broker, pump, monotonic_clock=ManualClock(), timeout_seconds=30.0
    )

    assert pump.calls == 3
    assert broker.flatten_in_progress is False


def test_run_startup_flatten_deadline_halts():
    from full_python.live.order_runner import run_startup_flatten

    broker = FakeFlattenBroker(resolve_after_pumps=10_000)
    clock = ManualClock()
    pump = CountingPump(broker, clock=clock, advance=31.0)

    with pytest.raises(TradovateStateError, match="deadline"):
        run_startup_flatten(
            broker, pump, monotonic_clock=clock, timeout_seconds=30.0
        )
    assert pump.calls == 1


def test_select_observe_account_explicit_single_and_ambiguous():
    from full_python.live.order_runner import require_account  # noqa: F401
    from full_python.live.runner import select_observe_account

    accounts = [{"id": 456, "name": "DEMO123"}, {"id": 999, "name": "OTHER"}]

    picked = select_observe_account(
        accounts, account_id="456", account_spec="DEMO123"
    )
    assert picked == {"id": 456, "name": "DEMO123"}

    only = select_observe_account([{"id": 7, "name": "SOLO"}])
    assert only == {"id": 7, "name": "SOLO"}

    with pytest.raises(SystemExit, match="multiple Tradovate accounts"):
        select_observe_account(accounts)
    with pytest.raises(SystemExit, match="BOTH"):
        select_observe_account(accounts, account_id="456")
    with pytest.raises(SystemExit, match="no Tradovate accounts"):
        select_observe_account([])
    with pytest.raises(TradovateStateError, match="named"):
        select_observe_account(accounts, account_id="456", account_spec="WRONG")
