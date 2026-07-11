"""Crash-safe event persistence for live sessions.

Same JSONL format as EventLedger.write_jsonl, written and flushed on
every append: a crash or Ctrl+C mid-session loses nothing already
recorded, which is what makes shutdown handling in the runner trivial.

Crash-safety requirement: one file per run. A restart (after crash or
shutdown) must use a NEW file with a non-colliding path; the runner is
responsible for choosing the filename. Attempting to open an existing
ledger file will raise FileExistsError.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from full_python.events import EventLedger, EventRecord, EventType


class PersistentEventLedger(EventLedger):
    def __init__(self, path) -> None:
        super().__init__()
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._handle = self._path.open("x", encoding="utf-8")
        except FileExistsError as e:
            raise FileExistsError(
                f"refusing to append to existing session ledger {self._path} -- pick a fresh file per run"
            ) from e

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
