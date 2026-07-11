from __future__ import annotations

from full_python.events import EventLedger, EventType
from full_python.live.persistence import PersistentEventLedger


def test_each_append_is_on_disk_immediately_without_close(tmp_path) -> None:
    path = tmp_path / "session" / "events.jsonl"
    ledger = PersistentEventLedger(path)

    ledger.append(EventType.BAR, timestamp_utc="2026-07-11T13:31:00Z",
                  payload={"close": 1.0})
    ledger.append(EventType.ORDER_INTENT, timestamp_utc="2026-07-11T13:32:00Z",
                  payload={"side": "buy"})
    # no close(): simulate a crash by reading the file right now
    loaded = EventLedger.read_jsonl(path)

    assert [r.to_dict() for r in loaded.records] == [r.to_dict() for r in ledger.records]
    assert loaded.records[1].payload == {"side": "buy"}


def test_behaves_as_a_normal_event_ledger_in_memory(tmp_path) -> None:
    ledger = PersistentEventLedger(tmp_path / "events.jsonl")
    record = ledger.append(EventType.BAR, timestamp_utc="2026-07-11T13:31:00Z")
    assert ledger.records == [record]
    assert record.event_id == "evt-00000001"
    ledger.close()


def test_refuses_to_reopen_an_existing_ledger_file(tmp_path) -> None:
    import pytest

    path = tmp_path / "events.jsonl"
    first = PersistentEventLedger(path)
    first.append(EventType.BAR, timestamp_utc="2026-07-11T13:31:00Z")
    first.close()

    with pytest.raises(FileExistsError, match="fresh file"):
        PersistentEventLedger(path)

    # the original record is untouched
    assert len(EventLedger.read_jsonl(path).records) == 1
