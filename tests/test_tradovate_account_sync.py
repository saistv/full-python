from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from full_python.execution.order_intent_journal import IntentState, OrderIntentJournal
from full_python.tradovate.account_sync import TradovateAccountHydrator
from full_python.tradovate.broker import BrokerExecutionState, TradovateBroker
from full_python.tradovate.config import DEMO_ENVIRONMENT, TradovateAdapterConfig
from full_python.tradovate.errors import TradovateStateError


def _config() -> TradovateAdapterConfig:
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
        commission_per_contract_round_trip=1.0,
        daily_loss_limit=1000.0,
    )


def _collections(**overrides):
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
            "tradeDate": {"year": 2026, "month": 7, "day": 7},
            "realizedPnL": -25.0,
        }],
        "accountRiskStatuses": [{
            "id": 456, "adminAction": "Normal", "liquidateOnly": False,
            "userTriggeredLiqOnly": False,
        }],
    }
    values.update(overrides)
    return values


class FakeWebSocket:
    def __init__(self, initial):
        self.initial = initial
        self.requests = []

    def request(self, endpoint, payload):
        self.requests.append((endpoint, payload))
        return self.initial


class FakeRest:
    def __init__(self, values):
        self.values = values

    def account_list(self):
        return self.values["accounts"]

    def contract_find(self, name):
        rows = [item for item in self.values["contracts"] if item["name"] == name]
        return rows[0] if rows else None

    def position_list(self):
        return self.values["positions"]

    def order_list(self):
        return self.values["orders"]

    def command_list(self):
        return self.values["commands"]

    def command_report_list(self):
        return self.values["commandReports"]

    def order_version_list(self):
        return self.values["orderVersions"]

    def fill_list(self):
        return self.values["fills"]

    def cash_balance_list(self):
        return self.values["cashBalances"]

    def account_risk_status_list(self):
        return self.values["accountRiskStatuses"]


class NoMutationRest(FakeRest):
    def order_place(self, body):
        raise AssertionError("hydration tests must not place orders")

    def order_cancel(self, body):
        raise AssertionError("hydration tests must not cancel orders")

    def order_liquidate_position(self, body):
        raise AssertionError("hydration tests must not liquidate")


def _hydrate(values=None):
    values = values or _collections()
    ws = FakeWebSocket(values)
    rest = NoMutationRest(values)
    snapshot = TradovateAccountHydrator(
        _config(), user_id=42, expected_trade_date=date(2026, 7, 7),
        websocket=ws, rest_client=rest,
    ).hydrate()
    return snapshot, ws, rest


def test_exact_stable_flat_snapshot_is_requested_and_normalized():
    snapshot, ws, _rest = _hydrate()

    assert ws.requests == [("user/syncrequest", {"users": [42]})]
    assert snapshot.account_id == 456
    assert snapshot.contract_id == 789
    assert snapshot.position is None
    assert snapshot.working_orders == ()
    assert snapshot.trade_date == "2026-07-07"
    assert snapshot.daily_realized_pnl == -25.0
    assert snapshot.entry_permitted is True


def test_order_enabled_broker_starts_closed_then_exact_flat_hydration_opens(tmp_path):
    values = _collections()
    snapshot, _ws, rest = _hydrate(values)
    journal = OrderIntentJournal(tmp_path / "orders.jsonl", run_id="run-a")
    broker = TradovateBroker(_config(), rest, intent_journal=journal)

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED
    broker.hydrate_account_state(snapshot)

    assert broker.execution_state == BrokerExecutionState.NORMAL
    assert broker.position is None
    assert broker.account_realized_pnl == -25.0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("closed", True, "closed"),
        ("readonly", True, "read-only"),
        ("futuresDisabled", True, "futures-disabled"),
    ],
)
def test_unsafe_account_flags_fail_closed(field, value, message):
    account = _collections()["accounts"][0] | {field: value}
    values = _collections(accounts=[account])
    with pytest.raises(TradovateStateError, match=message):
        _hydrate(values)


def test_open_position_is_recognized_but_cannot_open_entry_latch(tmp_path):
    positions = [{
        "id": 701, "accountId": 456, "contractId": 789,
        "netPos": 1, "netPrice": 20100.25,
    }]
    snapshot, _ws, rest = _hydrate(_collections(positions=positions))
    assert snapshot.position is not None
    assert snapshot.entry_permitted is False

    journal = OrderIntentJournal(tmp_path / "orders.jsonl", run_id="run-a")
    broker = TradovateBroker(_config(), rest, intent_journal=journal)
    with pytest.raises(TradovateStateError, match="inherited open position"):
        broker.hydrate_account_state(snapshot)
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_working_order_on_other_contract_in_configured_account_fails_closed():
    orders = [{
        "id": 801, "accountId": 456, "contractId": 790,
        "action": "Buy", "ordStatus": "Working", "admin": False,
    }]
    with pytest.raises(TradovateStateError, match="foreign-contract working order"):
        _hydrate(_collections(orders=orders))


def test_non_normal_risk_status_fails_closed():
    risk = [{
        "id": 456, "adminAction": "LockTradingImmediately",
        "liquidateOnly": None, "userTriggeredLiqOnly": False,
    }]
    with pytest.raises(TradovateStateError, match="risk status"):
        _hydrate(_collections(accountRiskStatuses=risk))


def test_missing_safety_collection_fails_closed():
    values = _collections()
    del values["orders"]
    ws = FakeWebSocket(values)
    with pytest.raises(TradovateStateError, match="orders"):
        TradovateAccountHydrator(
            _config(), user_id=42, expected_trade_date=date(2026, 7, 7),
            websocket=ws,
            rest_client=FakeRest(_collections()),
        ).hydrate()


def test_user_sync_and_rest_disagreement_fails_closed():
    sync = _collections()
    rest = _collections(positions=[{
        "id": 701, "accountId": 456, "contractId": 789,
        "netPos": 1, "netPrice": 20100.25,
    }])
    with pytest.raises(TradovateStateError, match="positions disagree"):
        TradovateAccountHydrator(
            _config(), user_id=42, expected_trade_date=date(2026, 7, 7),
            websocket=FakeWebSocket(sync),
            rest_client=FakeRest(rest),
        ).hydrate()


def test_duplicate_entity_id_fails_closed():
    account = _collections()["accounts"][0]
    values = _collections(accounts=[account, dict(account)])
    with pytest.raises(TradovateStateError, match="duplicate accounts"):
        _hydrate(values)


def test_fill_without_account_order_join_fails_closed():
    fills = [{
        "id": 901, "orderId": 999, "contractId": 789,
        "action": "Buy", "qty": 1, "price": 20000.0, "active": True,
    }]
    with pytest.raises(TradovateStateError, match="cannot be joined"):
        _hydrate(_collections(fills=fills))


def test_cash_balance_trade_date_must_match_expected_session():
    stale = _collections()["cashBalances"][0] | {
        "tradeDate": {"year": 2026, "month": 7, "day": 6},
    }
    with pytest.raises(TradovateStateError, match="cash balance trade date"):
        _hydrate(_collections(cashBalances=[stale]))


def test_unknown_order_status_fails_closed():
    orders = [{
        "id": 801, "accountId": 456, "contractId": 789,
        "action": "Buy", "ordStatus": "Mystery", "admin": False,
    }]
    with pytest.raises(TradovateStateError, match="unknown order status"):
        _hydrate(_collections(orders=orders))


def test_liquidation_custom_tag_is_available_for_journal_correlation():
    orders = [{
        "id": 801, "accountId": 456, "contractId": 789,
        "action": "Sell", "ordStatus": "Filled", "admin": False,
    }]
    commands = [{
        "id": 802, "orderId": 801, "commandType": "New",
        "commandStatus": "AtExecution", "customTag50": "fp-liquidation",
        "isAutomated": True,
    }]

    snapshot, _ws, _rest = _hydrate(_collections(
        orders=orders,
        commands=commands,
    ))

    assert snapshot.commands_by_client_id["fp-liquidation"]["id"] == 802


def test_acknowledged_history_reopens_only_with_exact_broker_correlation(tmp_path):
    client_id = "fp-restart-entry"
    orders = [{
        "id": 801, "accountId": 456, "contractId": 789,
        "action": "Buy", "ordStatus": "Filled", "admin": False,
    }]
    commands = [{
        "id": 802, "orderId": 801, "commandType": "New",
        "commandStatus": "AtExecution", "clOrdId": client_id,
        "isAutomated": True,
    }]
    snapshot, _ws, rest = _hydrate(_collections(
        orders=orders,
        commands=commands,
    ))
    journal = OrderIntentJournal(tmp_path / "orders.jsonl", run_id="run-restart")
    pending = journal.begin(
        role="entry",
        account_id=456,
        contract_id=789,
        client_operation_id=client_id,
        body={"clOrdId": client_id},
    )
    journal.transition(
        pending.intent_id,
        IntentState.ACKNOWLEDGED,
        broker_order_id="801",
    )
    broker = TradovateBroker(_config(), rest, intent_journal=journal)

    broker.hydrate_account_state(snapshot)

    assert broker.execution_state == BrokerExecutionState.NORMAL
    assert journal.latest_by_intent[pending.intent_id].state == IntentState.RECONCILED


def test_legacy_acknowledged_history_without_client_id_stays_closed(tmp_path):
    orders = [{
        "id": 801, "accountId": 456, "contractId": 789,
        "action": "Buy", "ordStatus": "Filled", "admin": False,
    }]
    snapshot, _ws, rest = _hydrate(_collections(orders=orders))
    journal = OrderIntentJournal(tmp_path / "orders.jsonl", run_id="run-legacy")
    pending = journal.begin(
        role="entry", account_id=456, contract_id=789, body={"symbol": "NQU6"},
    )
    journal.transition(
        pending.intent_id,
        IntentState.ACKNOWLEDGED,
        broker_order_id="801",
    )
    broker = TradovateBroker(_config(), rest, intent_journal=journal)

    with pytest.raises(TradovateStateError, match="client operation ID"):
        broker.hydrate_account_state(snapshot)

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_acknowledged_history_with_mismatched_command_stays_closed(tmp_path):
    orders = [{
        "id": 801, "accountId": 456, "contractId": 789,
        "action": "Buy", "ordStatus": "Filled", "admin": False,
    }]
    commands = [{
        "id": 802, "orderId": 801, "commandType": "New",
        "commandStatus": "AtExecution", "clOrdId": "fp-other",
        "isAutomated": True,
    }]
    snapshot, _ws, rest = _hydrate(_collections(
        orders=orders,
        commands=commands,
    ))
    journal = OrderIntentJournal(tmp_path / "orders.jsonl", run_id="run-mismatch")
    pending = journal.begin(
        role="entry",
        account_id=456,
        contract_id=789,
        client_operation_id="fp-expected",
        body={"clOrdId": "fp-expected"},
    )
    journal.transition(
        pending.intent_id,
        IntentState.ACKNOWLEDGED,
        broker_order_id="801",
    )
    broker = TradovateBroker(_config(), rest, intent_journal=journal)

    with pytest.raises(TradovateStateError, match="broker command"):
        broker.hydrate_account_state(snapshot)

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_fractional_entity_identity_is_not_coerced_to_an_integer():
    orders = [{
        "id": 801.5, "accountId": 456, "contractId": 789,
        "action": "Buy", "ordStatus": "Filled", "admin": False,
    }]
    with pytest.raises(TradovateStateError, match="invalid id"):
        _hydrate(_collections(orders=orders))


def test_overlength_broker_client_identifier_fails_closed():
    orders = [{
        "id": 801, "accountId": 456, "contractId": 789,
        "action": "Buy", "ordStatus": "Filled", "admin": False,
    }]
    commands = [{
        "id": 802, "orderId": 801, "commandType": "New",
        "commandStatus": "AtExecution", "clOrdId": "x" * 65,
        "isAutomated": True,
    }]
    with pytest.raises(TradovateStateError, match="no longer than 64"):
        _hydrate(_collections(orders=orders, commands=commands))


def test_fill_contract_must_match_its_joined_account_order():
    orders = [{
        "id": 801, "accountId": 456, "contractId": 789,
        "action": "Buy", "ordStatus": "Filled", "admin": False,
    }]
    fills = [{
        "id": 901, "orderId": 801, "contractId": 790,
        "action": "Buy", "qty": 1, "price": 20000.0, "active": True,
    }]
    with pytest.raises(TradovateStateError, match="does not match order"):
        _hydrate(_collections(orders=orders, fills=fills))


def test_unknown_order_action_fails_closed():
    orders = [{
        "id": 801, "accountId": 456, "contractId": 789,
        "action": "Hold", "ordStatus": "Filled", "admin": False,
    }]
    with pytest.raises(TradovateStateError, match="unknown order action"):
        _hydrate(_collections(orders=orders))


def test_string_boolean_account_flag_is_not_coerced():
    account = _collections()["accounts"][0] | {"closed": "false"}
    with pytest.raises(TradovateStateError, match="closed must be boolean"):
        _hydrate(_collections(accounts=[account]))


def test_hydration_snapshot_rejects_nonfinite_realized_pnl():
    snapshot, _ws, _rest = _hydrate()
    with pytest.raises(TradovateStateError, match="daily_realized_pnl"):
        replace(snapshot, daily_realized_pnl=float("nan"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_id", True),
        ("account_id", 456.0),
        ("contract_id", True),
        ("contract_id", 789.0),
    ],
)
def test_hydration_snapshot_identity_requires_strict_integers(field, value):
    snapshot, _ws, _rest = _hydrate()
    with pytest.raises(TradovateStateError, match="IDs must be positive integers"):
        replace(snapshot, **{field: value})
