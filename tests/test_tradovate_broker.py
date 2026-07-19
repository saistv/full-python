from dataclasses import dataclass, replace
from typing import Optional

import pytest

from full_python.data.sessions import classify_timestamp
from full_python.execution.broker_protocol import (
    Acked,
    BrokerPosition,
    Canceled,
    Filled,
    PartialFilled,
    Rejected,
)
from full_python.execution.order_intent_journal import IntentState, OrderIntentJournal
from full_python.models import Fill, MarketBar, OrderIntent, StrategyResult
from full_python.risk.limits import RiskLimits
from full_python.tradovate.account_sync import AccountHydrationSnapshot
from full_python.tradovate.broker import (
    BrokerExecutionState,
    TradovateBroker,
    TradovateRawEvent,
)
from full_python.tradovate.config import DEMO_ENVIRONMENT, TradovateAdapterConfig
from full_python.tradovate.errors import (
    TradovateConfigError,
    TradovateOrderSafetyError,
    TradovateStateError,
)


@dataclass(frozen=True)
class RecordedIntent:
    intent_id: str
    role: str
    state: IntentState
    client_operation_id: Optional[str] = None
    broker_order_id: Optional[str] = None
    detail: Optional[str] = None


class RecordingIntentJournal:
    def __init__(self):
        self.records = []
        self.latest_by_intent = {}

    @property
    def unresolved_intents(self):
        unresolved = {
            IntentState.SUBMISSION_PENDING,
            IntentState.REQUEST_ACCEPTED,
            IntentState.SUBMISSION_UNKNOWN,
        }
        return {
            key: value
            for key, value in self.latest_by_intent.items()
            if value.state in unresolved
        }

    @property
    def has_history(self):
        return bool(self.records)

    def begin(
        self, *, role, account_id, contract_id, body, client_operation_id=None
    ):
        record = RecordedIntent(
            intent_id=f"test:intent:{len(self.latest_by_intent) + 1:08d}",
            role=role,
            state=IntentState.SUBMISSION_PENDING,
            client_operation_id=client_operation_id,
        )
        self.records.append(record)
        self.latest_by_intent[record.intent_id] = record
        return record

    def transition(self, intent_id, state, *, broker_order_id=None, detail=None):
        previous = self.latest_by_intent[intent_id]
        record = RecordedIntent(
            intent_id=intent_id,
            role=previous.role,
            state=state,
            client_operation_id=previous.client_operation_id,
            broker_order_id=broker_order_id,
            detail=detail,
        )
        self.records.append(record)
        self.latest_by_intent[intent_id] = record
        return record


class FakeRestClient:
    def __init__(self, journal=None):
        self.placed = []
        self.canceled = []
        self.liquidations = []
        # queue of order_place responses; each call pops one (default ids 101, 102, ...)
        self.order_place_responses = []
        self._auto_id = 100
        self.order_place_error = None      # set to an exception to make order_place raise
        self.order_cancel_error = None     # set to an exception to make order_cancel raise
        self.liquidate_error = None
        self.liquidation_responses = []    # queue; each call pops one when present
        self.journal = journal
        self.post_boundaries = []

    def _record_boundary(self, operation):
        if self.journal is not None:
            pending = self.journal.records[-1]
            self.post_boundaries.append((operation, pending.role, pending.state))

    def order_place(self, body):
        self._record_boundary("order_place")
        if self.order_place_error is not None:
            error, self.order_place_error = self.order_place_error, None
            raise error
        self.placed.append(body)
        if self.order_place_responses:
            return self.order_place_responses.pop(0)
        self._auto_id += 1
        return {"orderId": self._auto_id}

    def order_cancel(self, body):
        self._record_boundary("order_cancel")
        if self.order_cancel_error is not None:
            error, self.order_cancel_error = self.order_cancel_error, None
            raise error
        self.canceled.append(body)
        return {}

    def order_liquidate_position(self, body):
        self._record_boundary("liquidate")
        if self.liquidate_error is not None:
            error, self.liquidate_error = self.liquidate_error, None
            raise error
        assert set(body) == {
            "accountId", "contractId", "admin", "isAutomated", "customTag50",
        }
        self.liquidations.append(body)
        if self.liquidation_responses:
            return self.liquidation_responses.pop(0)
        self._auto_id += 1
        return {"orderId": self._auto_id}


def _cfg(order_enabled=False, flatten_enabled=False, daily_loss_limit=1000.0):
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT,
        account_spec="DEMO123",
        account_id=456,
        root_symbol="NQ",
        contract_symbol="NQU6",
        contract_id=789,
        order_enabled=order_enabled,
        flatten_enabled=flatten_enabled,
        dollar_point_value=20.0,
        commission_per_contract_round_trip=1.0,
        daily_loss_limit=daily_loss_limit,
    )


_RISK_LIMITS = RiskLimits(max_contracts=1, flatten_minutes_et=959, rth_entries_only=True)


def _new_broker(config, rest=None, journal=None):
    journal = journal or RecordingIntentJournal()
    rest = rest or FakeRestClient(journal)
    rest.journal = journal
    broker = TradovateBroker(
        config,
        rest,
        intent_journal=journal,
        risk_limits=_RISK_LIMITS if config.order_enabled else None,
    )
    if config.order_enabled and not journal.has_history:
        broker.hydrate_account_state(_flat_hydration_snapshot())
    return broker


def _flat_hydration_snapshot(*, daily_realized_pnl=0.0, trade_date="2026-07-07"):
    return AccountHydrationSnapshot(
        account_id=456,
        account_spec="DEMO123",
        contract_id=789,
        contract_symbol="NQU6",
        position=None,
        working_orders=(),
        orders_by_id={},
        commands_by_client_id={},
        trade_date=trade_date,
        daily_realized_pnl=daily_realized_pnl,
        entry_permitted=True,
    )


def _terminal_snapshot_for_journal(
    journal, *, daily_realized_pnl=0.0, canceled_requests=()
):
    commands_by_client_id = {}
    orders_by_id = {}
    for record in journal.latest_by_intent.values():
        if record.state != IntentState.ACKNOWLEDGED:
            continue
        orders_by_id[record.broker_order_id] = {
            "id": int(record.broker_order_id),
            "ordStatus": "Filled",
        }
        commands_by_client_id[record.client_operation_id] = {
            "id": int(record.broker_order_id) + 1000,
            "orderId": int(record.broker_order_id),
            "isAutomated": True,
        }
    for body in canceled_requests:
        order_id = int(body["orderId"])
        orders_by_id[str(order_id)] = {
            "id": order_id,
            "ordStatus": "Canceled",
        }
        commands_by_client_id[body["clOrdId"]] = {
            "id": order_id + 2000,
            "orderId": order_id,
            "isAutomated": True,
        }
    return replace(
        _flat_hydration_snapshot(daily_realized_pnl=daily_realized_pnl),
        orders_by_id=orders_by_id,
        commands_by_client_id=commands_by_client_id,
    )


def _without_client_operation_id(body, field):
    value = body[field]
    assert value.startswith("fp-")
    assert len(value) <= 64
    return {key: item for key, item in body.items() if key != field}


def _assert_cancel_request(body, order_id):
    assert _without_client_operation_id(body, "clOrdId") == {
        "orderId": order_id,
        "isAutomated": True,
    }


def _assert_liquidation_request(body):
    assert _without_client_operation_id(body, "customTag50") == {
        "accountId": 456,
        "contractId": 789,
        "admin": False,
        "isAutomated": True,
    }


def _bar():
    return MarketBar(
        timestamp_utc="2026-07-07T14:32:00Z",
        symbol="NQU6",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.25,
        volume=1.0,
    )


def _session(bar=None):
    return classify_timestamp((bar or _bar()).timestamp_utc)


def _entry_result(bar=None, side="buy", metadata=None, quantity=1):
    bar = bar or _bar()
    if metadata is None:
        # A protective stop must sit on the adverse side of the reference
        # price or the shared RiskManager veto (correctly) rejects it.
        metadata = {"stop_price": 95.0 if side == "buy" else 105.5}
    return StrategyResult(order_intents=(
        OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=side,
            quantity=quantity,
            reason="adaptive_trend",
            metadata=metadata,
        ),
    ))


def _fill_event(order_id, action="Buy", qty=1, price=100.25, ts="2026-07-07T14:32:00Z", reason=""):
    return TradovateRawEvent(kind="fill", data={
        "orderId": order_id, "action": action, "qty": qty,
        "price": price, "timestamp": ts, "reason": reason,
        "accountId": 456, "contractId": 789,
    })


def _entered_broker(rest=None, side="buy", price=100.25, config=None):
    """Broker with a filled entry: order 101 placed, filled at `price`."""
    rest = rest or FakeRestClient()
    broker = _new_broker(
        config or _cfg(order_enabled=True, flatten_enabled=True),
        rest,
    )
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
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    assert len(rest.placed) == 1
    assert _without_client_operation_id(rest.placed[0], "clOrdId") == {
        "accountSpec": "DEMO123",
        "accountId": 456,
        "action": "Buy",
        "symbol": "NQU6",
        "orderQty": 1,
        "orderType": "Market",
        "isAutomated": True,
    }
    assert broker.poll_events() == [Acked(order_id="101")]


def test_repeated_entry_while_first_entry_is_working_submits_once():
    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    market_entries = [body for body in rest.placed if body["orderType"] == "Market"]
    assert len(market_entries) == 1
    assert broker.execution_state == BrokerExecutionState.ENTRY_PENDING_FILL
    rejects = [event for event in broker.poll_events() if isinstance(event, Rejected)]
    assert rejects == [
        Rejected(order_id="", reason="position_already_open")  # sim-identical veto
    ]


def test_live_enabled_entry_requires_stop_price_metadata():
    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()

    with pytest.raises(TradovateOrderSafetyError, match="stop_price"):
        broker.apply_strategy_result(bar, _session(bar), _entry_result(bar, metadata={}))

    assert rest.placed == []


def test_stale_hydration_blocks_direct_strategy_submission():
    from full_python.tradovate.errors import TradovateStateError

    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    next_day = replace(_bar(), timestamp_utc="2026-07-08T14:32:00Z")

    with pytest.raises(TradovateStateError, match="active session"):
        broker.apply_strategy_result(
            next_day,
            _session(next_day),
            _entry_result(next_day),
        )

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED
    assert rest.placed == []


def test_hydrated_account_daily_loss_blocks_entry_before_rest():
    rest = FakeRestClient()
    journal = RecordingIntentJournal()
    broker = TradovateBroker(
        _cfg(order_enabled=True, flatten_enabled=True),
        rest,
        intent_journal=journal,
        risk_limits=_RISK_LIMITS,
    )
    broker.hydrate_account_state(_flat_hydration_snapshot(
        daily_realized_pnl=-1000.0,
    ))

    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    assert broker.daily_limit_hit is True
    assert rest.placed == []
    assert broker.poll_events() == [
        Rejected(order_id="", reason="daily_limit")  # sim-identical veto (P1-7)
    ]


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


def test_entry_fill_emits_exactly_one_strategy_fill_with_intent_reason():
    broker, _rest = _entered_broker()

    assert broker.poll_strategy_feedback() == [Fill(
        timestamp_utc="2026-07-07T14:32:00Z",
        symbol="NQU6",
        side="buy",
        quantity=1,
        price=100.25,
        reason="adaptive_trend",
        metadata={"broker_order_id": "101"},
    )]
    assert broker.poll_strategy_feedback() == []


def test_partial_fill_event_requires_reconciliation_and_halts():
    broker, _rest = _entered_broker()

    from full_python.tradovate.errors import TradovateStateError

    with pytest.raises(TradovateStateError, match="partial fill"):
        broker.ingest_raw_event(TradovateRawEvent(
            kind="partial_fill",
            data={
                "orderId": 102,
                "action": "Sell",
                "qty": 1,
                "remaining": 2,
                "price": 100.25,
                "timestamp": "2026-07-07T14:32:00Z",
                "accountId": 456,
                "contractId": 789,
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
        data={
            "accountId": 456,
            "contractId": 789,
            "side": "long",
            "qty": 1,
            "price": 100.25,
        },
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

    assert len(rest.canceled) == 1
    _assert_cancel_request(rest.canceled[0], 102)
    assert rest.liquidations == []  # staged (P0-2): cancel confirms first
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    assert len(rest.liquidations) == 1
    _assert_liquidation_request(rest.liquidations[0])
    liquidation_records = [r for r in rest.journal.records if r.role == "liquidation"]
    assert [r.state for r in liquidation_records] == [
        IntentState.SUBMISSION_PENDING,
        IntentState.ACKNOWLEDGED,
    ]


def test_flatten_contract_cannot_be_retargeted_by_current_bar_symbol():
    broker, rest = _entered_broker()
    wrong_contract_bar = replace(_bar(), symbol="NQZ6")

    broker.flatten(wrong_contract_bar, "supervisor_halt")
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))

    assert len(rest.liquidations) == 1
    _assert_liquidation_request(rest.liquidations[0])


def test_repeated_flatten_does_not_duplicate_working_liquidation():
    broker, rest = _entered_broker()

    broker.flatten(_bar(), "supervisor_halt")
    broker.flatten(_bar(), "supervisor_halt")

    assert len(rest.canceled) == 1  # staged: one cancel, no duplicate flatten
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    broker.flatten(_bar(), "supervisor_halt")

    assert len(rest.liquidations) == 1
    liquidation_pending = [
        record for record in rest.journal.records
        if record.role == "liquidation" and record.state == IntentState.SUBMISSION_PENDING
    ]
    assert len(liquidation_pending) == 1


def test_unknown_liquidation_outcome_cannot_retry():
    from full_python.tradovate.errors import TradovateRequestError, TradovateStateError

    broker, rest = _entered_broker()
    rest.liquidate_error = TradovateRequestError("timeout_after_acceptance")

    broker.flatten(_bar(), "supervisor_halt")
    with pytest.raises(TradovateStateError, match="outcome unknown"):
        broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    broker.flatten(_bar(), "supervisor_halt")

    assert len(rest.liquidations) == 0
    assert len([boundary for boundary in rest.post_boundaries if boundary[0] == "liquidate"]) == 1
    assert rest.journal.records[-1].state == IntentState.SUBMISSION_UNKNOWN


def test_multi_contract_live_entry_is_forbidden_until_partial_fills_are_modeled():
    broker, _rest = _entered_broker()

    with pytest.raises(TradovateOrderSafetyError, match="quantity must equal 1"):
        broker.apply_strategy_result(
            _bar(), _session(), _entry_result(quantity=2)
        )


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


def test_repeated_entry_after_fill_and_protection_is_rejected_before_rest():
    broker, rest = _entered_broker()
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    market_entries = [body for body in rest.placed if body["orderType"] == "Market"]
    assert len(market_entries) == 1
    rejects = [event for event in broker.poll_events() if isinstance(event, Rejected)]
    assert rejects == [
        Rejected(order_id="", reason="position_already_open")  # sim-identical veto
    ]


def test_entry_is_rejected_while_strategy_exit_waits_for_stop_cancel():
    broker, rest = _entered_broker()
    broker.apply_strategy_result(_bar(), _session(), _exit_result())
    assert broker.execution_state == BrokerExecutionState.EXIT_PENDING_CANCEL

    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    market_entries = [body for body in rest.placed if body["orderType"] == "Market"]
    assert len(market_entries) == 1
    rejects = [event for event in broker.poll_events() if isinstance(event, Rejected)]
    assert rejects == [
        Rejected(order_id="", reason="position_already_open")  # sim-identical veto
    ]


def test_confirmed_entry_cancel_returns_to_stable_flat_for_later_signal():
    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 101}))

    assert broker.execution_state == BrokerExecutionState.NORMAL
    broker.apply_strategy_result(_bar(), _session(), _entry_result())
    assert len([body for body in rest.placed if body["orderType"] == "Market"]) == 2


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

    with pytest.raises(TradovateConfigError, match="contract_symbol"):
        TradovateBroker(
            replace(
                _cfg(order_enabled=True, flatten_enabled=True),
                contract_symbol=None,
                contract_id=None,
            ),
            FakeRestClient(),
        )

    with pytest.raises(TradovateConfigError, match="intent_journal"):
        TradovateBroker(
            _cfg(order_enabled=True, flatten_enabled=True),
            FakeRestClient(),
        )


def test_entry_intent_is_pending_before_rest_and_acknowledged_before_mapping():
    journal = RecordingIntentJournal()
    rest = FakeRestClient(journal)
    broker = _new_broker(
        _cfg(order_enabled=True, flatten_enabled=True),
        rest,
        journal,
    )

    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    assert rest.post_boundaries == [
        ("order_place", "entry", IntentState.SUBMISSION_PENDING),
    ]
    assert [(record.role, record.state, record.broker_order_id) for record in journal.records] == [
        ("entry", IntentState.SUBMISSION_PENDING, None),
        ("entry", IntentState.ACKNOWLEDGED, "101"),
    ]
    assert journal.records[0].client_operation_id == rest.placed[0]["clOrdId"]
    assert journal.records[1].client_operation_id == rest.placed[0]["clOrdId"]


def test_preexisting_unresolved_intent_starts_recovery_latched_and_cannot_post():
    journal = RecordingIntentJournal()
    journal.begin(
        role="entry",
        account_id=456,
        contract_id=789,
        body={"symbol": "NQU6"},
    )
    rest = FakeRestClient(journal)
    broker = _new_broker(
        _cfg(order_enabled=True, flatten_enabled=True),
        rest,
        journal,
    )

    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED
    assert rest.placed == []
    assert broker.poll_events() == [
        Rejected(order_id="", reason="entry_not_stable_flat")
    ]


def test_preexisting_acknowledged_intent_also_requires_restart_hydration():
    journal = RecordingIntentJournal()
    pending = journal.begin(
        role="entry",
        account_id=456,
        contract_id=789,
        body={"symbol": "NQU6"},
    )
    journal.transition(
        pending.intent_id,
        IntentState.ACKNOWLEDGED,
        broker_order_id="101",
    )
    rest = FakeRestClient(journal)
    broker = _new_broker(
        _cfg(order_enabled=True, flatten_enabled=True),
        rest,
        journal,
    )

    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED
    assert rest.placed == []


def test_reopened_real_journal_keeps_broker_entry_latch_closed(tmp_path):
    path = tmp_path / "orders.jsonl"
    first = OrderIntentJournal(path, run_id="run-restart")
    first.begin(
        role="entry",
        account_id=456,
        contract_id=789,
        body={"symbol": "NQU6"},
    )
    first.close()
    reopened = OrderIntentJournal(path, run_id="run-restart")
    rest = FakeRestClient()
    broker = TradovateBroker(
        _cfg(order_enabled=True, flatten_enabled=True),
        rest,
        intent_journal=reopened,
        risk_limits=_RISK_LIMITS,
    )

    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED
    assert rest.placed == []
    reopened.close()


def test_entry_symbol_must_match_exact_configured_contract_before_rest():
    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    result = StrategyResult(order_intents=(
        OrderIntent.market_entry(
            timestamp_utc=_bar().timestamp_utc,
            symbol="NQ",
            side="buy",
            quantity=1,
            reason="wrong_contract",
            metadata={"stop_price": 95.0},
        ),
    ))

    with pytest.raises(TradovateOrderSafetyError, match="contract symbol"):
        broker.apply_strategy_result(_bar(), _session(), result)

    assert rest.placed == []


def test_entry_fill_submits_protective_stop_at_frozen_price():
    broker, rest = _entered_broker()

    stop_bodies = [b for b in rest.placed if b.get("orderType") == "Stop"]
    assert len(stop_bodies) == 1
    assert _without_client_operation_id(stop_bodies[0], "clOrdId") == {
        "accountSpec": "DEMO123",
        "accountId": 456,
        "action": "Sell",           # opposite of the long entry
        "symbol": "NQU6",
        "orderQty": 1,
        "orderType": "Stop",
        "stopPrice": 95.0,          # frozen at the entry intent's stop_price
        "isAutomated": True,
    }
    acks = [e for e in broker.poll_events() if isinstance(e, Acked)]
    assert [a.order_id for a in acks] == ["101", "102"]  # entry, then stop


def test_protective_stop_rest_failure_flattens_and_raises():
    from full_python.tradovate.errors import TradovateRequestError, TradovateStateError

    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
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
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    broker.ingest_raw_event(TradovateRawEvent(
        kind="reject", data={"orderId": 101, "reason": "outside_market_hours"},
    ))

    rejects = [e for e in broker.poll_events() if isinstance(e, Rejected)]
    assert rejects == [Rejected(order_id="101", reason="outside_market_hours")]
    assert broker.position is None
    assert rest.liquidations == []   # entry rejection needs no flatten
    assert broker.execution_state == BrokerExecutionState.NORMAL

    broker.apply_strategy_result(_bar(), _session(), _entry_result())
    assert len([body for body in rest.placed if body["orderType"] == "Market"]) == 2


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

    assert len(rest.canceled) == 1
    _assert_cancel_request(rest.canceled[0], 102)
    assert broker.execution_state == BrokerExecutionState.EXIT_PENDING_CANCEL
    cancel_records = [r for r in rest.journal.records if r.role == "cancel"]
    assert [r.state for r in cancel_records] == [
        IntentState.SUBMISSION_PENDING,
        IntentState.REQUEST_ACCEPTED,
    ]
    # A REST-accepted cancel is only a request. No close may coexist with the
    # stop before the asynchronous cancellation event confirms final state.
    assert [b for b in rest.placed if b["orderType"] == "Market"] == [rest.placed[0]]

    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    assert broker.execution_state == BrokerExecutionState.EXIT_PENDING_FILL
    cancel_records = [r for r in rest.journal.records if r.role == "cancel"]
    assert cancel_records[-1].state == IntentState.CONFIRMED
    exit_records = [r for r in rest.journal.records if r.role == "exit"]
    assert [r.state for r in exit_records] == [
        IntentState.SUBMISSION_PENDING,
        IntentState.ACKNOWLEDGED,
    ]
    close_bodies = [b for b in rest.placed if b["orderType"] == "Market"][1:]
    assert len(close_bodies) == 1
    assert _without_client_operation_id(close_bodies[0], "clOrdId") == {
        "accountSpec": "DEMO123",
        "accountId": 456,
        "action": "Sell",
        "symbol": "NQU6",
        "orderQty": 1,
        "orderType": "Market",
        "isAutomated": True,
    }
    # exit fill closes the trade with the strategy's reason
    broker.ingest_raw_event(_fill_event(103, action="Sell", price=101.25,
                                        ts="2026-07-07T14:33:00Z"))
    assert broker.position is None
    assert broker.execution_state == BrokerExecutionState.NORMAL


def test_exit_fill_emits_exactly_one_fill_derived_closed_trade_feedback():
    broker, _rest = _entered_broker(price=100.0)
    broker.poll_strategy_feedback()  # consume the entry fill feedback
    broker.apply_strategy_result(_bar(), _session(), _exit_result(reason="atf_flip"))
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    broker.ingest_raw_event(_fill_event(
        103,
        action="Sell",
        price=101.25,
        ts="2026-07-07T14:33:00Z",
    ))

    feedback = broker.poll_strategy_feedback()
    assert len(feedback) == 1
    trade = feedback[0]
    assert trade == broker.trades[-1]
    assert trade.exit_reason == "atf_flip"
    assert broker.poll_strategy_feedback() == []


def test_strategy_exit_while_flat_is_a_no_op():
    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
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


def test_stop_fill_wins_cancel_race_and_suppresses_market_exit():
    broker, rest = _entered_broker()
    broker.apply_strategy_result(_bar(), _session(), _exit_result())

    broker.ingest_raw_event(_fill_event(
        102, action="Sell", price=95.0, ts="2026-07-07T14:33:00Z"
    ))

    assert broker.position is None
    assert broker.execution_state == BrokerExecutionState.NORMAL
    assert [b for b in rest.placed if b["orderType"] == "Market"] == [rest.placed[0]]


def test_exit_rejection_after_confirmed_stop_cancel_emergency_flattens_and_halts():
    from full_python.tradovate.errors import TradovateStateError

    broker, rest = _entered_broker()
    broker.apply_strategy_result(_bar(), _session(), _exit_result())
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))

    with pytest.raises(TradovateStateError, match="exit order 103 rejected"):
        broker.ingest_raw_event(TradovateRawEvent(
            kind="reject", data={"orderId": 103, "reason": "market_halted"},
        ))

    assert len(rest.liquidations) == 1
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_exit_fill_quantity_mismatch_raises_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()

    with pytest.raises(TradovateStateError, match="quantity"):
        broker.ingest_raw_event(_fill_event(102, action="Sell", qty=3))


def test_unsolicited_protective_stop_cancel_flattens_and_halts():
    from full_python.tradovate.errors import TradovateStateError

    broker, rest = _entered_broker()

    with pytest.raises(TradovateStateError, match="canceled unexpectedly"):
        broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))

    cancels = [e for e in broker.poll_events() if isinstance(e, Canceled)]
    assert cancels == [Canceled(order_id="102")]
    assert len(rest.liquidations) == 1


def test_flatten_while_flat_is_a_no_op():
    rest = FakeRestClient()
    broker = _new_broker(_cfg(flatten_enabled=True), rest)

    broker.flatten(_bar(), "supervisor_halt")

    assert rest.liquidations == []


def test_flatten_while_flat_cancels_working_entry_and_late_fill_recovers():
    from full_python.tradovate.errors import TradovateStateError

    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    broker.flatten(_bar(), "supervisor_halt")
    assert len(rest.canceled) == 1
    _assert_cancel_request(rest.canceled[0], 101)
    assert rest.liquidations == []

    with pytest.raises(TradovateStateError, match="filled after flatten cancellation"):
        broker.ingest_raw_event(_fill_event(101))
    assert len(rest.liquidations) == 1


def test_repeated_flatten_while_entry_cancel_pending_does_not_cancel_twice():
    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    broker.flatten(_bar(), "supervisor_halt")
    broker.flatten(_bar(), "supervisor_halt")

    assert len(rest.canceled) == 1
    _assert_cancel_request(rest.canceled[0], 101)


def test_entry_failure_response_is_rejected_without_key_error():
    rest = FakeRestClient()
    rest.order_place_responses = [{"failureReason": "outside_market_hours"}]
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)

    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    assert broker.poll_events() == [
        Rejected(order_id="", reason="outside_market_hours")
    ]
    assert rest.journal.records[-1].state == IntentState.REJECTED


def test_entry_transport_error_maps_to_halting_state_error():
    from full_python.tradovate.errors import TradovateRequestError, TradovateStateError

    rest = FakeRestClient()
    rest.order_place_error = TradovateRequestError("timeout")
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)

    with pytest.raises(TradovateStateError, match="outcome unknown"):
        broker.apply_strategy_result(_bar(), _session(), _entry_result())

    assert rest.journal.records[-1].state == IntentState.SUBMISSION_UNKNOWN
    broker.apply_strategy_result(_bar(), _session(), _entry_result())
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED
    assert rest.placed == []
    assert broker.poll_events() == [
        Rejected(order_id="", reason="entry_not_stable_flat")
    ]


def test_non_tradovate_exception_after_pending_is_still_durable_unknown():
    from full_python.tradovate.errors import TradovateStateError

    rest = FakeRestClient()
    rest.order_place_error = RuntimeError("socket vanished")
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)

    with pytest.raises(TradovateStateError, match="outcome unknown"):
        broker.apply_strategy_result(_bar(), _session(), _entry_result())

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED
    assert rest.journal.records[-1].state == IntentState.SUBMISSION_UNKNOWN


def test_entry_malformed_response_is_unknown_and_never_retried():
    from full_python.tradovate.errors import TradovateStateError

    rest = FakeRestClient()
    rest.order_place_responses = [{}]
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)

    with pytest.raises(TradovateStateError, match="missing orderId"):
        broker.apply_strategy_result(_bar(), _session(), _entry_result())

    assert rest.journal.records[-1].state == IntentState.SUBMISSION_UNKNOWN
    broker.apply_strategy_result(_bar(), _session(), _entry_result())
    assert len(rest.placed) == 1


def test_flatten_while_short_cancels_stop_then_liquidates():
    broker, rest = _entered_broker(side="sell")

    broker.flatten(_bar(), "daily_limit")

    assert len(rest.canceled) == 1
    _assert_cancel_request(rest.canceled[0], 102)
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    assert len(rest.liquidations) == 1
    # the liquidation order is registered: its fill is a KNOWN id
    liq_id = 103
    broker.ingest_raw_event(_fill_event(liq_id, action="Buy", price=99.0,
                                        ts="2026-07-07T14:34:00Z"))
    assert broker.position is None


def test_process_bar_open_returns_realized_plus_unrealized_gross():
    broker, _rest = _entered_broker(price=100.0)  # long 1 @ 100
    bar = _bar()  # close 100.25

    session_pnl = broker.process_bar_open(bar, _session(bar))

    assert session_pnl == pytest.approx(0.25 * 20.0)  # unrealized gross only
    assert broker.daily_limit_hit is False


def test_realized_losses_accumulate_into_session_pnl_and_trades():
    broker, _rest = _entered_broker(price=100.0)
    # stop fills 30pts against: -600 gross, -601 net
    broker.ingest_raw_event(_fill_event(102, action="Sell", price=70.0,
                                        ts="2026-07-07T14:35:00Z"))
    bar = _bar()

    session_pnl = broker.process_bar_open(bar, _session(bar))

    assert session_pnl == pytest.approx(-601.0)
    assert len(broker.trades) == 1
    assert broker.trades[0].net_pnl == pytest.approx(-601.0)
    assert broker.trades[0].exit_reason == "stop"
    assert broker.trades[0].session_date == "2026-07-07"
    assert broker.daily_limit_hit is False  # -601 > -1000


def test_rehydration_does_not_double_count_locally_paired_realized_pnl():
    broker, rest = _entered_broker(price=100.0)
    broker.ingest_raw_event(_fill_event(
        102,
        action="Sell",
        price=70.0,
        ts="2026-07-07T14:35:00Z",
    ))
    snapshot = _terminal_snapshot_for_journal(
        rest.journal,
        daily_realized_pnl=-601.0,
    )

    broker.hydrate_account_state(snapshot)

    assert broker.account_realized_pnl == pytest.approx(-601.0)
    assert broker.process_bar_open(_bar(), _session()) == pytest.approx(-601.0)


def test_stable_flat_rehydration_clears_terminal_liquidation_state():
    broker, rest = _entered_broker(price=100.25)
    broker.flatten(_bar(), "operator_flatten")
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    broker.ingest_raw_event(_fill_event(
        103,
        action="Sell",
        price=99.0,
        ts="2026-07-07T14:35:00Z",
    ))
    broker.hydrate_account_state(_terminal_snapshot_for_journal(
        rest.journal,
        daily_realized_pnl=-26.0,
        canceled_requests=rest.canceled,
    ))

    broker.apply_strategy_result(_bar(), _session(), _entry_result())

    market_entries = [
        body for body in rest.placed if body["orderType"] == "Market"
    ]
    assert len(market_entries) == 2
    assert broker.execution_state == BrokerExecutionState.ENTRY_PENDING_FILL


def test_daily_loss_breach_sets_flag_and_flattens_open_position():
    broker, rest = _entered_broker(price=100.0)
    # first round trip: -601 net realized
    broker.ingest_raw_event(_fill_event(102, action="Sell", price=70.0,
                                        ts="2026-07-07T14:35:00Z"))
    # second entry, long 1 @ 100 (order 103 entry, 104 stop)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))
    broker.ingest_raw_event(_fill_event(103, price=100.0, ts="2026-07-07T14:36:00Z"))
    # bar closes 25pts against: unrealized -500 -> session -1101 <= -1000
    losing_bar = MarketBar(timestamp_utc="2026-07-07T14:37:00Z", symbol="NQU6",
                           open=100.0, high=100.0, low=75.0, close=75.0, volume=1.0)

    session_pnl = broker.process_bar_open(losing_bar, _session(losing_bar))

    assert session_pnl == pytest.approx(-601.0 - 500.0)
    assert broker.daily_limit_hit is True
    assert any(body["orderId"] == 104 for body in rest.canceled)
    assert rest.liquidations == []              # staged (P0-2): cancel confirms first
    broker.ingest_raw_event(_cancel_event(104))
    assert len(rest.liquidations) == 1          # DLL breach flattened after confirmation


def test_daily_loss_breach_with_flatten_disabled_halts():
    from full_python.tradovate.errors import TradovateStateError

    # orders disabled so the flag pairing rule allows flatten_enabled=False;
    # build the losing position via direct fill ingestion on a manual order.
    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))
    broker.ingest_raw_event(_fill_event(101, price=100.0))
    # simulate a misconfigured runtime by flipping the internal config object
    # is not possible (frozen); instead assert the code path via a broker
    # whose position was built while flatten was enabled and a NEW broker is
    # not constructible in that state -- so this test pins the guard directly:
    broker._config = _cfg(order_enabled=False, flatten_enabled=False)
    losing_bar = MarketBar(timestamp_utc="2026-07-07T14:37:00Z", symbol="NQU6",
                           open=100.0, high=100.0, low=40.0, close=40.0, volume=1.0)

    with pytest.raises(TradovateStateError, match="flatten"):
        broker.process_bar_open(losing_bar, _session(losing_bar))


def test_session_rollover_requires_fresh_account_hydration_when_flat():
    from full_python.tradovate.errors import TradovateStateError

    broker, rest = _entered_broker(price=100.0)
    # lose big enough to breach: stop fill 60pts against = -1201 net
    broker.ingest_raw_event(_fill_event(102, action="Sell", price=40.0,
                                        ts="2026-07-07T14:35:00Z"))
    bar = _bar()
    broker.process_bar_open(bar, _session(bar))
    assert broker.daily_limit_hit is True
    broker.note_bar_processed(bar, _session(bar))

    next_day = MarketBar(timestamp_utc="2026-07-08T14:31:00Z", symbol="NQU6",
                         open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0)
    with pytest.raises(TradovateStateError, match="fresh broker account hydration"):
        broker.process_bar_open(next_day, _session(next_day))

    assert broker.daily_limit_hit is True
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_session_rollover_accepts_hydration_for_incoming_session():
    broker = _new_broker(
        _cfg(order_enabled=True, flatten_enabled=True),
        FakeRestClient(),
    )
    first_day = _bar()
    broker.process_bar_open(first_day, _session(first_day))
    broker.note_bar_processed(first_day, _session(first_day))
    broker._daily_limit_hit = True

    next_day = MarketBar(
        timestamp_utc="2026-07-08T14:31:00Z",
        symbol="NQU6",
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=1.0,
    )
    broker.hydrate_account_state(_flat_hydration_snapshot(
        trade_date="2026-07-08",
        daily_realized_pnl=0.0,
    ))

    assert broker.process_bar_open(next_day, _session(next_day)) == 0.0
    assert broker.daily_limit_hit is False
    assert broker.execution_state == BrokerExecutionState.NORMAL


def test_session_rollover_with_open_position_halts():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker(price=100.0)
    bar = _bar()
    broker.process_bar_open(bar, _session(bar))
    broker.note_bar_processed(bar, _session(bar))
    next_day = MarketBar(timestamp_utc="2026-07-08T14:31:00Z", symbol="NQU6",
                         open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0)

    with pytest.raises(TradovateStateError, match="session rollover"):
        broker.process_bar_open(next_day, _session(next_day))


def test_position_snapshot_with_position_while_fill_derived_flat_raises():
    from full_python.tradovate.errors import TradovateStateError

    broker = TradovateBroker(_cfg(), FakeRestClient())

    with pytest.raises(TradovateStateError, match="contradicts"):
        broker.ingest_raw_event(TradovateRawEvent(
            kind="position",
            data={
                "accountId": 456,
                "contractId": 789,
                "side": "long",
                "qty": 1,
                "price": 100.25,
            },
        ))


def test_flat_position_snapshot_while_fill_derived_open_raises():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()

    with pytest.raises(TradovateStateError, match="contradicts"):
        broker.ingest_raw_event(TradovateRawEvent(
            kind="position",
            data={"accountId": 456, "contractId": 789, "side": "flat", "qty": 0},
        ))


@pytest.mark.parametrize(
    "data, message",
    [
        ({"contractId": 789, "side": "flat", "qty": 0}, "accountId"),
        ({"accountId": 456, "side": "flat", "qty": 0}, "contractId"),
        (
            {"accountId": 999, "contractId": 789, "side": "flat", "qty": 0},
            "foreign account",
        ),
        (
            {"accountId": 456, "contractId": 790, "side": "flat", "qty": 0},
            "foreign contract",
        ),
    ],
)
def test_position_event_requires_exact_account_and_contract_identity(data, message):
    from full_python.tradovate.errors import TradovateStateError

    broker = TradovateBroker(_cfg(), FakeRestClient())

    with pytest.raises(TradovateStateError, match=message):
        broker.ingest_raw_event(TradovateRawEvent(kind="position", data=data))


@pytest.mark.parametrize(
    "identity, message",
    [
        ({"contractId": 789}, "accountId"),
        ({"accountId": 456}, "contractId"),
        ({"accountId": 999, "contractId": 789}, "foreign account"),
        ({"accountId": 456, "contractId": 790}, "foreign contract"),
    ],
)
def test_fill_event_requires_exact_account_and_contract_identity(identity, message):
    from full_python.tradovate.errors import TradovateStateError

    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    broker.apply_strategy_result(_bar(), _session(), _entry_result())
    data = {
        "orderId": 101,
        "action": "Buy",
        "qty": 1,
        "price": 100.25,
        "timestamp": "2026-07-07T14:32:00Z",
        **identity,
    }

    with pytest.raises(TradovateStateError, match=message):
        broker.ingest_raw_event(TradovateRawEvent(kind="fill", data=data))


def test_rest_position_snapshot_disagreement_raises():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()   # fill-derived: long 1

    broker.reconcile_rest_positions([{
        "accountId": 456, "contractId": 789, "netPos": 1, "netPrice": 100.5,
    }])  # match: ok

    with pytest.raises(TradovateStateError, match="REST position"):
        broker.reconcile_rest_positions([{
            "accountId": 456, "contractId": 789, "netPos": -2, "netPrice": 100.5,
        }])

    with pytest.raises(TradovateStateError, match="REST position"):
        broker.reconcile_rest_positions([])  # broker flat, we are long


@pytest.mark.parametrize(
    "positions, message",
    [
        ([{"contractId": 789, "netPos": 0}], "accountId"),
        ([{"accountId": 456, "netPos": 0}], "contractId"),
        ([{"accountId": 999, "contractId": 789, "netPos": 0}], "foreign account"),
        ([{"accountId": 456, "contractId": 790, "netPos": 0}], "foreign contract"),
        (
            [
                {"accountId": 456, "contractId": 789, "netPos": 0},
                {"accountId": 456, "contractId": 789, "netPos": 0},
            ],
            "duplicate",
        ),
    ],
)
def test_rest_position_snapshot_rejects_ambiguous_identity(positions, message):
    from full_python.tradovate.errors import TradovateStateError

    broker = TradovateBroker(_cfg(), FakeRestClient())

    with pytest.raises(TradovateStateError, match=message):
        broker.reconcile_rest_positions(positions)


def test_rest_position_snapshot_multiple_open_items_raises_even_if_net_flat():
    # A +1/-1 pair (e.g. a contract-roll straddle, or a duplicated/
    # contradictory feed) must never be summed down to a false flat --
    # more than one item with a nonzero netPos is itself an anomaly,
    # even against a fresh (fill-derived flat) broker.
    from full_python.tradovate.errors import TradovateStateError

    broker = TradovateBroker(_cfg(), FakeRestClient())

    with pytest.raises(TradovateStateError, match="REST position"):
        broker.reconcile_rest_positions([
            {"accountId": 456, "contractId": 789, "netPos": 1, "netPrice": 100.0},
            {"accountId": 456, "contractId": 790, "netPos": -1, "netPrice": 99.0},
        ])


def test_flat_position_snapshot_while_flat_passes():
    # Common real-world path: fill-derived flat + a WS "position" event
    # reporting flat -- must not raise (previously crashed inside
    # _position_from_data with unsupported_position_side).
    broker = TradovateBroker(_cfg(), FakeRestClient())

    broker.ingest_raw_event(TradovateRawEvent(
        kind="position",
        data={"accountId": 456, "contractId": 789, "side": "flat", "qty": 0},
    ))


# ---------------------------------------------------------------------------
# Slice E: confirmed flatten protocol and session-close backstop
# (P0-2, P0-04, P1-5, P0-03 —
#  docs/decisions/2026-07-19-confirmed-flatten-session-boundaries.md)
# ---------------------------------------------------------------------------


def _cancel_event(order_id):
    return TradovateRawEvent(kind="cancel", data={"orderId": order_id})


def _bar_at(ts, **overrides):
    return replace(_bar(), timestamp_utc=ts, **overrides)


def _session_with_close(bar, *, minutes, close):
    return replace(
        _session(bar),
        minutes_from_midnight_et=minutes,
        rth_close_minutes_et=close,
    )


def test_flatten_with_working_stop_requests_cancel_and_defers_liquidation():
    broker, rest = _entered_broker()

    broker.flatten(_bar(), "daily_limit")

    assert len(rest.canceled) == 1
    _assert_cancel_request(rest.canceled[0], 102)
    assert rest.liquidations == []
    assert broker.execution_state == BrokerExecutionState.FLATTEN_PENDING_CANCEL


def test_flatten_cancel_failure_halts_and_keeps_stop_protection():
    from full_python.tradovate.errors import TradovateRequestError, TradovateStateError

    broker, rest = _entered_broker()
    rest.order_cancel_error = TradovateRequestError("cancel refused")

    with pytest.raises(TradovateStateError, match="could not cancel"):
        broker.flatten(_bar(), "daily_limit")

    assert rest.liquidations == []
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_confirmed_cancel_then_liquidation_then_flat_ends_normal():
    broker, rest = _entered_broker()
    broker.flatten(_bar(), "daily_limit")

    broker.ingest_raw_event(_cancel_event(102))

    assert len(rest.liquidations) == 1
    _assert_liquidation_request(rest.liquidations[0])
    assert broker.execution_state == BrokerExecutionState.FLATTEN_PENDING_FILL

    broker.ingest_raw_event(
        _fill_event(103, action="Sell", price=99.0, ts="2026-07-07T14:33:00Z")
    )

    assert broker.position is None
    assert broker.execution_state == BrokerExecutionState.NORMAL
    feedback = broker.poll_strategy_feedback()
    assert any(
        getattr(item, "exit_reason", None) == "daily_limit" for item in feedback
    )


def test_stop_fill_during_pending_cancel_never_double_closes():
    # P0-2's exact race: the protective stop fills before the cancel lands.
    broker, rest = _entered_broker()
    broker.flatten(_bar(), "daily_limit")

    broker.ingest_raw_event(
        _fill_event(102, action="Sell", price=95.0, ts="2026-07-07T14:33:00Z")
    )

    assert rest.liquidations == []  # liquidation never submitted: no reversal
    assert broker.position is None
    assert broker.execution_state == BrokerExecutionState.NORMAL


def test_flatten_liquidation_rejection_latches_without_second_liquidation():
    from full_python.tradovate.errors import TradovateStateError

    broker, rest = _entered_broker()
    broker.flatten(_bar(), "daily_limit")
    broker.ingest_raw_event(_cancel_event(102))
    assert len(rest.liquidations) == 1

    with pytest.raises(TradovateStateError, match="rejected"):
        broker.ingest_raw_event(TradovateRawEvent(kind="reject", data={
            "orderId": 103, "reason": "liquidation rejected",
        }))

    assert len(rest.liquidations) == 1  # no emergency re-attempt
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_flatten_resolution_with_residual_working_order_latches():
    from full_python.tradovate.broker import ROLE_EXIT, SubmittedOrder
    from full_python.tradovate.errors import TradovateStateError

    broker, rest = _entered_broker()
    broker.flatten(_bar(), "daily_limit")
    broker.ingest_raw_event(_cancel_event(102))
    # Adversarial injection: an unknown working order appears before the
    # liquidation fill. Resolution must refuse to declare the account flat.
    broker._orders["999"] = SubmittedOrder(
        order_id="999", role=ROLE_EXIT, side="sell", quantity=1, symbol="NQU6",
    )

    with pytest.raises(TradovateStateError, match="residual working order"):
        broker.ingest_raw_event(
            _fill_event(103, action="Sell", price=99.0, ts="2026-07-07T14:33:00Z")
        )

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_unresolved_flatten_on_a_later_bar_halts():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()
    broker.flatten(_bar(), "daily_limit")  # cancel requested, never confirmed

    later = _bar_at("2026-07-07T14:33:00Z")
    with pytest.raises(TradovateStateError, match="unresolved flatten"):
        broker.process_bar_open(later, _session(later))

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_dll_breach_runs_staged_flatten_and_blocks_entries_after_normal():
    broker, rest = _entered_broker()  # long 1 @ 100.25, DLL $1000, $20/pt
    adverse = _bar_at(
        "2026-07-07T14:33:00Z", open=51.0, high=51.0, low=49.0, close=50.0
    )

    broker.process_bar_open(adverse, _session(adverse))

    assert len(rest.canceled) == 1 and rest.liquidations == []
    broker.ingest_raw_event(_cancel_event(102))
    assert len(rest.liquidations) == 1
    broker.ingest_raw_event(
        _fill_event(103, action="Sell", price=50.0, ts="2026-07-07T14:34:00Z")
    )

    assert broker.position is None
    # P1-5: a routine confirmed flatten ends NORMAL, not RECOVERY_REQUIRED...
    assert broker.execution_state == BrokerExecutionState.NORMAL
    # ...while the DLL latch still blocks entries for the session.
    broker.poll_events()
    broker.apply_strategy_result(adverse, _session(adverse), _entry_result(adverse))
    assert broker.poll_events() == [
        Rejected(order_id="", reason="daily_limit")  # sim-identical veto (P1-7)
    ]


def test_early_close_day_triggers_backstop_flatten_at_close_minus_one():
    broker, rest = _entered_broker()
    bar = _bar_at("2026-07-07T17:14:00Z")
    session = _session_with_close(bar, minutes=13 * 60 + 14, close=13 * 60 + 15)

    broker.process_bar_open(bar, session)

    assert len(rest.canceled) == 1  # staged flatten began: stop cancel requested
    assert broker.execution_state == BrokerExecutionState.FLATTEN_PENDING_CANCEL


def test_normal_day_triggers_backstop_flatten_at_1559():
    broker, rest = _entered_broker()
    bar = _bar_at("2026-07-07T19:59:00Z")
    session = _session(bar)
    assert session.minutes_from_midnight_et == 15 * 60 + 59
    assert session.rth_close_minutes_et == 16 * 60

    broker.process_bar_open(bar, session)

    assert len(rest.canceled) == 1
    assert broker.execution_state == BrokerExecutionState.FLATTEN_PENDING_CANCEL


def test_backstop_does_not_fire_before_close_minus_one_or_when_flat():
    broker, rest = _entered_broker()
    early = _bar_at("2026-07-07T19:58:00Z")
    broker.process_bar_open(early, _session(early))
    assert rest.canceled == [] and rest.liquidations == []

    flat_rest = FakeRestClient()
    flat_broker = _new_broker(
        _cfg(order_enabled=True, flatten_enabled=True), flat_rest
    )
    late = _bar_at("2026-07-07T19:59:00Z")
    flat_broker.process_bar_open(late, _session(late))
    assert flat_rest.canceled == [] and flat_rest.liquidations == []


# ---------------------------------------------------------------------------
# Slice G1: shared RiskManager veto (audit P1-7) -- live refuses what sim refuses
# ---------------------------------------------------------------------------


def test_order_enabled_requires_risk_limits():
    with pytest.raises(TradovateConfigError, match="risk_limits"):
        TradovateBroker(
            _cfg(order_enabled=True, flatten_enabled=True),
            FakeRestClient(),
            intent_journal=RecordingIntentJournal(),
        )


def test_market_closed_session_vetoes_entry_before_any_post():
    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    closed_session = replace(_session(bar), rth_close_minutes_et=None)

    broker.apply_strategy_result(bar, closed_session, _entry_result(bar))

    assert rest.placed == []
    assert rest.journal.records == []  # vetoed before any journal activity
    assert broker.poll_events() == [Rejected(order_id="", reason="market_closed")]


def test_after_flatten_window_vetoes_entry_with_sim_reason():
    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar_at("2026-07-07T19:59:00Z")  # 15:59 ET

    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    assert rest.placed == []
    assert broker.poll_events() == [Rejected(order_id="", reason="after_flatten")]


def test_invalid_stop_vetoes_before_any_post():
    rest = FakeRestClient()
    broker = _new_broker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    result = _entry_result(
        bar, metadata={"stop_price": 100.5, "signal_price": 100.25}
    )

    broker.apply_strategy_result(bar, _session(bar), result)

    assert rest.placed == []
    assert broker.poll_events() == [Rejected(order_id="", reason="invalid_stop")]


# ---------------------------------------------------------------------------
# P1-8: startup inherited-state flatten (operator policy 2026-07-19: FLATTEN)
# ---------------------------------------------------------------------------


def _unhydrated_order_broker():
    journal = RecordingIntentJournal()
    rest = FakeRestClient(journal)
    broker = TradovateBroker(
        _cfg(order_enabled=True, flatten_enabled=True),
        rest,
        intent_journal=journal,
        risk_limits=_RISK_LIMITS,
    )
    return broker, rest


def _inherited_snapshot(position=True, orders=True, order_action="Sell"):
    return replace(
        _flat_hydration_snapshot(),
        position=BrokerPosition(side="long", quantity=1, entry_price=20100.25)
        if position else None,
        working_orders=({
            "id": 555, "ordStatus": "Working", "contractId": 789,
            "action": order_action, "orderQty": 1,
        },) if orders else (),
        entry_permitted=False,
    )


def test_startup_flatten_stages_cancel_liquidation_and_stays_recovery():
    broker, rest = _unhydrated_order_broker()

    broker.startup_flatten(
        _inherited_snapshot(), timestamp_utc="2026-07-20T13:31:00Z"
    )

    assert broker.flatten_in_progress is True
    assert len(rest.canceled) == 1
    _assert_cancel_request(rest.canceled[0], 555)
    assert rest.liquidations == []
    assert broker.execution_state == BrokerExecutionState.FLATTEN_PENDING_CANCEL

    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": "555"}))
    assert len(rest.liquidations) == 1
    _assert_liquidation_request(rest.liquidations[0])

    broker.ingest_raw_event(_fill_event(
        101, action="Sell", price=20050.0, ts="2026-07-20T13:31:05Z"
    ))

    assert broker.position is None
    assert broker.flatten_in_progress is False
    # No strategy trade is fabricated for an inherited position.
    assert broker.poll_strategy_feedback() == []
    # Entries stay closed until a FRESH stable-flat hydration...
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED
    broker.hydrate_account_state(_terminal_snapshot_for_journal(
        rest.journal, canceled_requests=rest.canceled,
    ))
    assert broker.execution_state == BrokerExecutionState.NORMAL


def test_inherited_stop_fill_race_resolves_without_liquidation():
    broker, rest = _unhydrated_order_broker()
    broker.startup_flatten(
        _inherited_snapshot(), timestamp_utc="2026-07-20T13:31:00Z"
    )

    broker.ingest_raw_event(_fill_event(
        "555", action="Sell", price=20050.0, ts="2026-07-20T13:31:02Z"
    ))

    assert rest.liquidations == []
    assert broker.position is None
    assert broker.flatten_in_progress is False
    assert broker.poll_strategy_feedback() == []
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_startup_flatten_requires_flatten_enabled():
    broker = TradovateBroker(_cfg(), FakeRestClient())
    with pytest.raises(TradovateStateError, match="flatten_enabled"):
        broker.startup_flatten(
            _inherited_snapshot(), timestamp_utc="2026-07-20T13:31:00Z"
        )


def test_startup_flatten_identity_mismatch_and_stable_flat_misuse_raise():
    broker, _rest = _unhydrated_order_broker()
    with pytest.raises(TradovateStateError, match="identity mismatch"):
        broker.startup_flatten(
            replace(_inherited_snapshot(), account_id=999),
            timestamp_utc="2026-07-20T13:31:00Z",
        )

    broker2, _rest2 = _unhydrated_order_broker()
    with pytest.raises(TradovateStateError, match="stable-flat"):
        broker2.startup_flatten(
            _flat_hydration_snapshot(), timestamp_utc="2026-07-20T13:31:00Z"
        )


def test_startup_flatten_cancel_failure_halts_latched():
    from full_python.tradovate.errors import TradovateRequestError

    broker, rest = _unhydrated_order_broker()
    rest.order_cancel_error = TradovateRequestError("cancel refused")

    with pytest.raises(TradovateStateError, match="could not cancel"):
        broker.startup_flatten(
            _inherited_snapshot(), timestamp_utc="2026-07-20T13:31:00Z"
        )

    assert rest.liquidations == []
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_review_2026_07_19_p0_4_exposure_increasing_inherited_order_refused():
    # An inherited BUY against an inherited long could ADD exposure: refused
    # at the boundary, before any cancel race can put it in flight.
    broker, _rest = _unhydrated_order_broker()
    with pytest.raises(TradovateStateError, match="increase exposure"):
        broker.startup_flatten(
            _inherited_snapshot(order_action="Buy"),
            timestamp_utc="2026-07-20T13:31:00Z",
        )

    # Orders with NO inherited position are refused outright: any fill
    # would create exposure from flat.
    broker2, _rest2 = _unhydrated_order_broker()
    with pytest.raises(TradovateStateError, match="increase exposure"):
        broker2.startup_flatten(
            _inherited_snapshot(position=False),
            timestamp_utc="2026-07-20T13:31:00Z",
        )


def test_review_2026_07_19_p0_4_multi_contract_inherited_position_refused():
    from full_python.execution.broker_protocol import BrokerPosition

    broker, _rest = _unhydrated_order_broker()
    snapshot = replace(
        _inherited_snapshot(orders=False),
        position=BrokerPosition(side="long", quantity=3, entry_price=20100.25),
    )
    with pytest.raises(TradovateStateError, match="MANUAL flatten"):
        broker.startup_flatten(snapshot, timestamp_utc="2026-07-20T13:31:00Z")
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_review_2026_07_19_p0_4a_inherited_fill_while_flat_adopts_then_flattens():
    from full_python.tradovate.broker import ROLE_INHERITED, SubmittedOrder

    broker, rest = _unhydrated_order_broker()
    broker._orders["555"] = SubmittedOrder(
        order_id="555", role=ROLE_INHERITED, side="buy", quantity=1,
        symbol="NQU6", reason="inherited",
    )

    with pytest.raises(TradovateStateError, match="filled while locally flat"):
        broker.ingest_raw_event(_fill_event(
            "555", action="Buy", price=20150.0, ts="2026-07-20T13:31:02Z"
        ))

    # the REAL exposure was adopted and an emergency liquidation submitted
    assert len(rest.liquidations) == 1
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


# ---------------------------------------------------------------------------
# P2-5: session rollover vs cancel confirmation (row 19)
# ---------------------------------------------------------------------------


def test_rollover_with_confirmed_canceled_order_is_clean():
    from full_python.tradovate.broker import ROLE_PROTECTIVE_STOP, SubmittedOrder

    journal = RecordingIntentJournal()
    rest = FakeRestClient(journal)
    broker = TradovateBroker(_cfg(flatten_enabled=True), rest, intent_journal=journal)
    broker._orders["77"] = SubmittedOrder(
        order_id="77", role=ROLE_PROTECTIVE_STOP, side="sell", quantity=1,
        symbol="NQU6", status="canceled",
    )
    day1 = _bar()
    broker.process_bar_open(day1, _session(day1))
    broker.note_bar_processed(day1, _session(day1))

    day2 = _bar_at("2026-07-08T13:31:00Z")
    broker.process_bar_open(day2, _session(day2))  # no halt: cancel confirmed


def test_rollover_with_unconfirmed_cancel_still_halts_fail_closed():
    from full_python.tradovate.broker import ROLE_PROTECTIVE_STOP, SubmittedOrder

    journal = RecordingIntentJournal()
    rest = FakeRestClient(journal)
    broker = TradovateBroker(_cfg(flatten_enabled=True), rest, intent_journal=journal)
    broker._orders["77"] = SubmittedOrder(
        order_id="77", role=ROLE_PROTECTIVE_STOP, side="sell", quantity=1,
        symbol="NQU6", status="working",
    )
    day1 = _bar()
    broker.process_bar_open(day1, _session(day1))
    broker.note_bar_processed(day1, _session(day1))

    day2 = _bar_at("2026-07-08T13:31:00Z")
    with pytest.raises(TradovateStateError, match="backstop should have flattened"):
        broker.process_bar_open(day2, _session(day2))


# ---------------------------------------------------------------------------
# Review 2026-07-19 pins: P0-2 interleavings (single-close invariant)
# ---------------------------------------------------------------------------


def test_review_2026_07_19_p0_2a_duplicate_cancel_is_idempotent():
    broker, rest = _entered_broker()
    broker.apply_strategy_result(_bar(), _session(), _exit_result())
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    exits_placed = len(rest.placed)
    liq_before = len(rest.liquidations)
    broker.poll_events()

    # duplicate terminal event: must be a silent no-op, never an emergency
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))

    assert len(rest.placed) == exits_placed          # no new orders
    assert len(rest.liquidations) == liq_before      # no emergency liquidation
    assert broker.poll_events() == []                # no duplicate Canceled event
    assert broker._orders["103"].status == "working" # market exit still working


def test_review_2026_07_19_p0_2b_same_bar_flatten_and_exit_cancel_once():
    broker, rest = _entered_broker()
    backstop_bar = _bar_at("2026-07-07T19:59:00Z")

    broker.process_bar_open(backstop_bar, _session(backstop_bar))  # backstop flatten
    assert len(rest.canceled) == 1

    broker.poll_events()
    broker.apply_strategy_result(
        backstop_bar, _session(backstop_bar), _exit_result(backstop_bar)
    )

    assert len(rest.canceled) == 1  # no duplicate cancel POST
    cancel_intents = [
        r for r in rest.journal.records
        if r.role == "cancel" and r.state == IntentState.SUBMISSION_PENDING
    ]
    assert len(cancel_intents) == 1  # no orphaned journal intent
    assert broker.poll_events() == [
        Rejected(order_id="", reason="flatten_in_progress")
    ]


def test_review_2026_07_19_p0_2c_exit_rejection_during_flatten_emergency_flattens():
    broker, rest = _entered_broker()
    broker.apply_strategy_result(_bar(), _session(), _exit_result())
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    assert broker._orders["103"].status == "working"  # market exit in flight

    broker.flatten(_bar(), "daily_limit")  # flatten now awaits cancel of 103
    liq_before = len(rest.liquidations)

    with pytest.raises(TradovateStateError, match="rejected"):
        broker.ingest_raw_event(TradovateRawEvent(kind="reject", data={
            "orderId": 103, "reason": "exchange reject",
        }))

    # the position has no working close; the emergency MUST liquidate
    assert len(rest.liquidations) == liq_before + 1
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_review_2026_07_19_p0_3_rejected_liquidation_clears_for_explicit_retry():
    broker, rest = _entered_broker()
    rest.liquidation_responses = [{"failureReason": "market_closed"}]
    broker.flatten(_bar(), "daily_limit")

    with pytest.raises(TradovateStateError, match="liquidation response rejected"):
        broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))

    assert broker.flatten_in_progress is False   # latches cleared for retry
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED
    assert len(rest.liquidations) == 1

    # explicit operator retry is now possible and completes
    broker.flatten(_bar(), "daily_limit")
    assert len(rest.liquidations) == 2
    broker.ingest_raw_event(_fill_event(
        103, action="Sell", price=99.0, ts="2026-07-07T14:35:00Z"
    ))
    assert broker.position is None
    assert broker.flatten_in_progress is False


def test_review_2026_07_19_p1_3_resolved_flatten_clears_stale_pending_exit():
    broker, rest = _entered_broker()
    broker.apply_strategy_result(_bar(), _session(), _exit_result())
    assert broker.execution_state == BrokerExecutionState.EXIT_PENDING_CANCEL

    broker.flatten(_bar(), "supervisor_halt")  # reuses the requested cancel
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    liq_id = 103
    broker.ingest_raw_event(_fill_event(
        liq_id, action="Sell", price=99.0, ts="2026-07-07T14:35:00Z"
    ))

    assert broker.position is None
    assert broker.execution_state == BrokerExecutionState.NORMAL
    broker.poll_events()

    # the resolved state must be USABLE: a fresh entry places
    entries_before = len([b for b in rest.placed if b["orderType"] == "Market"])
    broker.apply_strategy_result(_bar(), _session(), _entry_result())
    entries_after = len([b for b in rest.placed if b["orderType"] == "Market"])
    assert entries_after == entries_before + 1


def test_review_2026_07_19_p1_3_stop_wins_race_then_fresh_hydration_reopens():
    broker, rest = _unhydrated_order_broker()
    broker.startup_flatten(
        _inherited_snapshot(), timestamp_utc="2026-07-20T13:31:00Z"
    )
    broker.ingest_raw_event(_fill_event(
        "555", action="Sell", price=20050.0, ts="2026-07-20T13:31:02Z"
    ))
    assert broker.position is None
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED

    # the cancel raced and LOST to the fill: order 555 is terminally Filled.
    cancel_body = rest.canceled[0]
    snapshot = replace(
        _flat_hydration_snapshot(),
        orders_by_id={"555": {"id": 555, "ordStatus": "Filled"}},
        commands_by_client_id={cancel_body["clOrdId"]: {
            "id": 2555, "orderId": 555, "isAutomated": True,
        }},
    )
    broker.hydrate_account_state(snapshot)
    assert broker.execution_state == BrokerExecutionState.NORMAL
