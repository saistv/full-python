from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any


class EventType(str, Enum):
    BAR = "bar"
    SIGNAL_DECISION = "signal_decision"
    REJECTION = "rejection"
    ORDER_INTENT = "order_intent"
    STOP_UPDATE = "stop_update"
    EXIT = "exit"
    RISK_VETO = "risk_veto"
    STATE_TRANSITION = "state_transition"
    FILL = "fill"
    TRADE_CLOSED = "trade_closed"


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    event_type: EventType
    timestamp_utc: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp_utc": self.timestamp_utc,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventRecord":
        return cls(
            event_id=str(data["event_id"]),
            event_type=EventType(str(data["event_type"])),
            timestamp_utc=str(data["timestamp_utc"]),
            payload=dict(data["payload"]),
        )


class EventLedger:
    def __init__(self) -> None:
        self.records: list[EventRecord] = []

    def append(
        self,
        event_type: EventType,
        *,
        timestamp_utc: str,
        payload: dict[str, Any] | None = None,
    ) -> EventRecord:
        event_id = f"evt-{len(self.records) + 1:08d}"
        record = EventRecord(
            event_id=event_id,
            event_type=event_type,
            timestamp_utc=timestamp_utc,
            payload={} if payload is None else dict(payload),
        )
        self.records.append(record)
        return record

    def write_jsonl(self, path: str | Path) -> None:
        output_path = Path(path)
        with output_path.open("w", encoding="utf-8") as handle:
            for record in self.records:
                handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")

    @classmethod
    def read_jsonl(cls, path: str | Path) -> "EventLedger":
        ledger = cls()
        input_path = Path(path)
        with input_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                ledger.records.append(EventRecord.from_dict(json.loads(line)))
        return ledger
