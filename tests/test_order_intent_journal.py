from __future__ import annotations

import json

import pytest

import full_python.execution.order_intent_journal as journal_module
from full_python.execution.order_intent_journal import (
    IntentJournalError,
    IntentState,
    OrderIntentJournal,
)


def _begin(journal: OrderIntentJournal):
    return journal.begin(
        role="entry",
        account_id=456,
        contract_id=789,
        body={"accountId": 456, "symbol": "NQU6", "orderQty": 1},
    )


def test_pending_is_durable_and_reopens_as_unresolved(tmp_path):
    path = tmp_path / "orders.jsonl"
    journal = OrderIntentJournal(path, run_id="run-a")

    pending = _begin(journal)
    journal.close()

    reopened = OrderIntentJournal(path, run_id="run-a")
    assert pending.state == IntentState.SUBMISSION_PENDING
    assert pending.intent_id == "run-a:intent:00000001"
    assert reopened.records == [pending]
    assert reopened.unresolved_intents == {pending.intent_id: pending}


def test_creation_and_every_append_call_fsync(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        journal_module.os,
        "fsync",
        lambda file_descriptor: calls.append(file_descriptor),
    )
    journal = OrderIntentJournal(tmp_path / "orders.jsonl", run_id="run-a")
    creation_calls = len(calls)

    _begin(journal)

    assert creation_calls == 2  # file contents, then parent-directory entry
    assert len(calls) == 3


def test_acknowledged_order_id_is_durable_and_resolves_submission(tmp_path):
    path = tmp_path / "orders.jsonl"
    journal = OrderIntentJournal(path, run_id="run-a")
    pending = _begin(journal)

    acknowledged = journal.transition(
        pending.intent_id,
        IntentState.ACKNOWLEDGED,
        broker_order_id="101",
    )
    journal.close()

    reopened = OrderIntentJournal(path, run_id="run-a")
    assert acknowledged.broker_order_id == "101"
    assert reopened.latest_by_intent[pending.intent_id] == acknowledged
    assert reopened.unresolved_intents == {}


def test_cancel_request_stays_unresolved_until_confirmed(tmp_path):
    journal = OrderIntentJournal(tmp_path / "orders.jsonl", run_id="run-a")
    pending = journal.begin(
        role="cancel",
        account_id=456,
        contract_id=789,
        body={"orderId": 102},
    )
    accepted = journal.transition(
        pending.intent_id,
        IntentState.REQUEST_ACCEPTED,
    )
    assert journal.unresolved_intents == {pending.intent_id: accepted}

    confirmed = journal.transition(pending.intent_id, IntentState.CONFIRMED)
    assert confirmed.state == IntentState.CONFIRMED
    assert journal.unresolved_intents == {}


def test_illegal_transition_fails_closed(tmp_path):
    journal = OrderIntentJournal(tmp_path / "orders.jsonl", run_id="run-a")
    pending = _begin(journal)
    journal.transition(pending.intent_id, IntentState.REJECTED, detail="risk")

    with pytest.raises(IntentJournalError, match="illegal intent transition"):
        journal.transition(pending.intent_id, IntentState.ACKNOWLEDGED)


def test_torn_final_record_is_truncated_to_verified_prefix(tmp_path):
    path = tmp_path / "orders.jsonl"
    journal = OrderIntentJournal(path, run_id="run-a")
    pending = _begin(journal)
    journal.close()
    verified_bytes = path.read_bytes()
    with path.open("ab") as handle:
        handle.write(b'{"schema_version":1,"run_id":"run-a"')

    reopened = OrderIntentJournal(path, run_id="run-a")

    assert reopened.records == [pending]
    assert path.read_bytes() == verified_bytes


def test_corruption_before_final_record_is_not_repaired(tmp_path):
    path = tmp_path / "orders.jsonl"
    journal = OrderIntentJournal(path, run_id="run-a")
    first = _begin(journal)
    journal.transition(first.intent_id, IntentState.ACKNOWLEDGED, broker_order_id="101")
    journal.close()
    lines = path.read_text().splitlines()
    path.write_text("not-json\n" + lines[1] + "\n")

    with pytest.raises(IntentJournalError, match="record 1"):
        OrderIntentJournal(path, run_id="run-a")


def test_complete_invalid_final_record_is_not_treated_as_torn(tmp_path):
    path = tmp_path / "orders.jsonl"
    journal = OrderIntentJournal(path, run_id="run-a")
    _begin(journal)
    journal.close()
    valid_prefix = path.read_bytes()
    with path.open("ab") as handle:
        handle.write(b"not-json\n")

    with pytest.raises(IntentJournalError, match="record 2"):
        OrderIntentJournal(path, run_id="run-a")

    path.write_bytes(valid_prefix)
    recovered = OrderIntentJournal(path, run_id="run-a")
    assert len(recovered.records) == 1
    recovered.close()


def test_hash_tampering_fails_closed(tmp_path):
    path = tmp_path / "orders.jsonl"
    journal = OrderIntentJournal(path, run_id="run-a")
    _begin(journal)
    journal.close()
    record = json.loads(path.read_text())
    record["account_id"] = 999
    path.write_text(json.dumps(record, sort_keys=True) + "\n")

    with pytest.raises(IntentJournalError, match="hash"):
        OrderIntentJournal(path, run_id="run-a")


def test_run_id_mismatch_fails_closed(tmp_path):
    path = tmp_path / "orders.jsonl"
    journal = OrderIntentJournal(path, run_id="run-a")
    _begin(journal)
    journal.close()

    with pytest.raises(IntentJournalError, match="run_id"):
        OrderIntentJournal(path, run_id="run-b")


def test_second_writer_is_rejected(tmp_path):
    path = tmp_path / "orders.jsonl"
    first = OrderIntentJournal(path, run_id="run-a")

    with pytest.raises(IntentJournalError, match="already locked"):
        OrderIntentJournal(path, run_id="run-a")

    first.close()


def test_body_digest_is_canonical_and_body_is_not_persisted(tmp_path):
    path = tmp_path / "orders.jsonl"
    journal = OrderIntentJournal(path, run_id="run-a")
    first = journal.begin(
        role="entry",
        account_id=456,
        contract_id=789,
        body={"b": 2, "a": 1},
    )
    second = journal.begin(
        role="entry",
        account_id=456,
        contract_id=789,
        body={"a": 1, "b": 2},
    )

    assert first.body_digest == second.body_digest
    persisted = path.read_text()
    assert '"body"' not in persisted
    assert '"a": 1' not in persisted
