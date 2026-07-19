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
