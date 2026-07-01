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

    @property
    def event_count(self) -> int:
        return len(self.records)

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


class StreamingEventLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")
        self._event_count = 0

    @property
    def event_count(self) -> int:
        return self._event_count

    def append(
        self,
        event_type: EventType,
        *,
        timestamp_utc: str,
        payload: dict[str, Any] | None = None,
    ) -> EventRecord:
        self._event_count += 1
        record = EventRecord(
            event_id=f"evt-{self._event_count:08d}",
            event_type=event_type,
            timestamp_utc=timestamp_utc,
            payload={} if payload is None else dict(payload),
        )
        self._handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
        return record

    def close(self) -> None:
        self._handle.close()
