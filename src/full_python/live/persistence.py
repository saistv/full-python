"""Crash-safe event persistence for live sessions.

Same JSONL format as EventLedger.write_jsonl, written and flushed on
every append: a crash or Ctrl+C mid-session loses nothing already
recorded, which is what makes shutdown handling in the runner trivial.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from full_python.events import EventLedger, EventRecord, EventType


class PersistentEventLedger(EventLedger):
    def __init__(self, path) -> None:
        super().__init__()
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8")

    def append(
        self,
        event_type: EventType,
        *,
        timestamp_utc: str,
        payload: Optional[dict] = None,
    ) -> EventRecord:
        record = super().append(event_type, timestamp_utc=timestamp_utc, payload=payload)
        self._handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
        self._handle.flush()
        return record

    def close(self) -> None:
        self._handle.close()
