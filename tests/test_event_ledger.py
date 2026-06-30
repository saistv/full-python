from full_python.events import EventLedger, EventRecord, EventType


def test_event_ledger_appends_records_in_order_with_stable_ids() -> None:
    ledger = EventLedger()

    first = ledger.append(
        EventType.BAR,
        timestamp_utc="2026-06-30T13:30:00Z",
        payload={"contract": "NQU2026", "close": 29350.25},
    )
    second = ledger.append(
        EventType.SIGNAL_DECISION,
        timestamp_utc="2026-06-30T13:31:00Z",
        payload={"decision": "rejected", "reason": "vwap_permission"},
    )

    assert first.event_id == "evt-00000001"
    assert second.event_id == "evt-00000002"
    assert ledger.records == [first, second]
    assert isinstance(first, EventRecord)
    assert first.payload["contract"] == "NQU2026"


def test_event_ledger_round_trips_jsonl(tmp_path) -> None:
    ledger = EventLedger()
    ledger.append(
        EventType.ORDER_INTENT,
        timestamp_utc="2026-06-30T13:31:00Z",
        payload={"symbol": "NQU2026", "side": "buy", "quantity": 1},
    )

    path = tmp_path / "events.jsonl"
    ledger.write_jsonl(path)

    loaded = EventLedger.read_jsonl(path)

    assert loaded.records == ledger.records
