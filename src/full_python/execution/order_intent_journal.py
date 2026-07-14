"""Durable, hash-linked logical intents for broker-mutating requests."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional, Protocol

from full_python.execution.state_machine import ExecutionInvariantError


SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64


class IntentJournalError(ExecutionInvariantError):
    pass


class IntentState(str, Enum):
    SUBMISSION_PENDING = "submission_pending"
    ACKNOWLEDGED = "acknowledged"
    REQUEST_ACCEPTED = "request_accepted"
    REJECTED = "rejected"
    SUBMISSION_UNKNOWN = "submission_unknown"
    CONFIRMED = "confirmed"
    RECONCILED = "reconciled"


UNRESOLVED_STATES = {
    IntentState.SUBMISSION_PENDING,
    IntentState.REQUEST_ACCEPTED,
    IntentState.SUBMISSION_UNKNOWN,
}


@dataclass(frozen=True)
class IntentRecord:
    schema_version: int
    run_id: str
    sequence: int
    intent_id: str
    role: str
    account_id: int
    contract_id: int
    body_digest: str
    state: IntentState
    previous_hash: str
    record_hash: str
    broker_order_id: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        if not include_hash:
            data.pop("record_hash")
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IntentRecord":
        return cls(
            schema_version=int(data["schema_version"]),
            run_id=str(data["run_id"]),
            sequence=int(data["sequence"]),
            intent_id=str(data["intent_id"]),
            role=str(data["role"]),
            account_id=int(data["account_id"]),
            contract_id=int(data["contract_id"]),
            body_digest=str(data["body_digest"]),
            state=IntentState(str(data["state"])),
            previous_hash=str(data["previous_hash"]),
            record_hash=str(data["record_hash"]),
            broker_order_id=(
                None
                if data.get("broker_order_id") is None
                else str(data["broker_order_id"])
            ),
            detail=None if data.get("detail") is None else str(data["detail"]),
        )


class IntentJournal(Protocol):
    def begin(
        self,
        *,
        role: str,
        account_id: int,
        contract_id: int,
        body: Any,
    ) -> IntentRecord: ...

    def transition(
        self,
        intent_id: str,
        state: IntentState,
        *,
        broker_order_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> IntentRecord: ...

    @property
    def unresolved_intents(self) -> dict[str, IntentRecord]: ...

    @property
    def has_history(self) -> bool: ...


class OrderIntentJournal:
    """Append-only order intent journal with fsync and verified tail recovery."""

    def __init__(self, path: str | Path, *, run_id: str) -> None:
        if not run_id.strip():
            raise IntentJournalError("run_id must be nonblank")
        self._path = Path(path)
        self._run_id = run_id
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[IntentRecord] = []
        self.latest_by_intent: dict[str, IntentRecord] = {}
        self._next_intent_number = 1
        if self._path.exists():
            self._handle = self._path.open("r+b")
            self._lock()
            try:
                self._recover()
            except Exception:
                self._unlock_and_close()
                raise
            self._handle.seek(0, os.SEEK_END)
        else:
            self._handle = self._path.open("x+b")
            self._lock()
            try:
                self._sync()
                self._sync_parent_directory()
            except Exception:
                self._unlock_and_close()
                raise

    @property
    def unresolved_intents(self) -> dict[str, IntentRecord]:
        return {
            intent_id: record
            for intent_id, record in self.latest_by_intent.items()
            if record.state in UNRESOLVED_STATES
        }

    @property
    def has_history(self) -> bool:
        return bool(self.records)

    def begin(
        self,
        *,
        role: str,
        account_id: int,
        contract_id: int,
        body: Any,
    ) -> IntentRecord:
        if not role.strip():
            raise IntentJournalError("intent role must be nonblank")
        if account_id <= 0 or contract_id <= 0:
            raise IntentJournalError("intent account_id and contract_id must be positive")
        intent_id = f"{self._run_id}:intent:{self._next_intent_number:08d}"
        self._next_intent_number += 1
        record = self._new_record(
            intent_id=intent_id,
            role=role,
            account_id=account_id,
            contract_id=contract_id,
            body_digest=_body_digest(body),
            state=IntentState.SUBMISSION_PENDING,
        )
        self._append(record)
        return record

    def transition(
        self,
        intent_id: str,
        state: IntentState,
        *,
        broker_order_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> IntentRecord:
        previous = self.latest_by_intent.get(intent_id)
        if previous is None:
            raise IntentJournalError(f"unknown logical intent {intent_id}")
        if state not in _ALLOWED_TRANSITIONS[previous.state]:
            raise IntentJournalError(
                f"illegal intent transition {previous.state.value} -> {state.value}"
            )
        if state == IntentState.ACKNOWLEDGED and not broker_order_id:
            raise IntentJournalError("acknowledged intent requires broker_order_id")
        record = self._new_record(
            intent_id=previous.intent_id,
            role=previous.role,
            account_id=previous.account_id,
            contract_id=previous.contract_id,
            body_digest=previous.body_digest,
            state=state,
            broker_order_id=broker_order_id,
            detail=_bounded_detail(detail),
        )
        self._append(record)
        return record

    def close(self) -> None:
        self._unlock_and_close()

    def _unlock_and_close(self) -> None:
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()

    def _lock(self) -> None:
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            raise IntentJournalError(
                f"intent journal is already locked by another writer: {self._path}"
            ) from exc

    def _new_record(
        self,
        *,
        intent_id: str,
        role: str,
        account_id: int,
        contract_id: int,
        body_digest: str,
        state: IntentState,
        broker_order_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> IntentRecord:
        fields = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self._run_id,
            "sequence": len(self.records) + 1,
            "intent_id": intent_id,
            "role": role,
            "account_id": account_id,
            "contract_id": contract_id,
            "body_digest": body_digest,
            "state": state,
            "previous_hash": self.records[-1].record_hash if self.records else GENESIS_HASH,
            "broker_order_id": broker_order_id,
            "detail": detail,
        }
        hash_fields = dict(fields)
        hash_fields["state"] = state.value
        return IntentRecord(record_hash=_record_hash(hash_fields), **fields)

    def _append(self, record: IntentRecord) -> None:
        self._handle.write((_canonical_json(record.to_dict()) + "\n").encode("utf-8"))
        self._sync()
        self.records.append(record)
        self.latest_by_intent[record.intent_id] = record

    def _sync(self) -> None:
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def _sync_parent_directory(self) -> None:
        directory_fd = os.open(str(self._path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _recover(self) -> None:
        self._handle.seek(0)
        raw = self._handle.read()
        lines = raw.splitlines(keepends=True)
        verified_bytes = 0
        for index, line in enumerate(lines, start=1):
            is_last = index == len(lines)
            if not line.endswith(b"\n"):
                if is_last:
                    self._truncate(verified_bytes)
                    break
                raise IntentJournalError(f"intent journal record {index} is torn")
            try:
                decoded = json.loads(line.decode("utf-8"))
                record = IntentRecord.from_dict(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise IntentJournalError(
                    f"intent journal record {index} is invalid"
                ) from exc
            self._validate_loaded_record(record, index)
            self.records.append(record)
            self.latest_by_intent[record.intent_id] = record
            verified_bytes += len(line)
        pending_count = sum(
            1 for record in self.records if record.state == IntentState.SUBMISSION_PENDING
        )
        self._next_intent_number = pending_count + 1

    def _validate_loaded_record(self, record: IntentRecord, index: int) -> None:
        if record.schema_version != SCHEMA_VERSION:
            raise IntentJournalError(
                f"intent journal record {index} has unsupported schema_version"
            )
        if record.run_id != self._run_id:
            raise IntentJournalError(
                f"intent journal record {index} run_id does not match {self._run_id!r}"
            )
        if record.sequence != index:
            raise IntentJournalError(
                f"intent journal record {index} breaks sequence"
            )
        expected_previous = self.records[-1].record_hash if self.records else GENESIS_HASH
        if record.previous_hash != expected_previous:
            raise IntentJournalError(
                f"intent journal record {index} breaks previous hash chain"
            )
        expected_hash = _record_hash(record.to_dict(include_hash=False))
        if record.record_hash != expected_hash:
            raise IntentJournalError(f"intent journal record {index} hash mismatch")
        previous = self.latest_by_intent.get(record.intent_id)
        if record.state == IntentState.SUBMISSION_PENDING:
            if previous is not None:
                raise IntentJournalError(
                    f"intent journal record {index} duplicates logical intent"
                )
            return
        if previous is None:
            raise IntentJournalError(
                f"intent journal record {index} transitions unknown intent"
            )
        if record.state not in _ALLOWED_TRANSITIONS[previous.state]:
            raise IntentJournalError(
                f"intent journal record {index} has illegal intent transition"
            )
        identity = (
            record.role,
            record.account_id,
            record.contract_id,
            record.body_digest,
        )
        previous_identity = (
            previous.role,
            previous.account_id,
            previous.contract_id,
            previous.body_digest,
        )
        if identity != previous_identity:
            raise IntentJournalError(
                f"intent journal record {index} changes intent identity"
            )
        if record.state == IntentState.ACKNOWLEDGED and not record.broker_order_id:
            raise IntentJournalError(
                f"intent journal record {index} acknowledgment lacks broker order id"
            )

    def _truncate(self, length: int) -> None:
        self._handle.seek(length)
        self._handle.truncate()
        self._sync()


_ALLOWED_TRANSITIONS = {
    IntentState.SUBMISSION_PENDING: {
        IntentState.ACKNOWLEDGED,
        IntentState.REQUEST_ACCEPTED,
        IntentState.REJECTED,
        IntentState.SUBMISSION_UNKNOWN,
        IntentState.RECONCILED,
    },
    IntentState.REQUEST_ACCEPTED: {
        IntentState.CONFIRMED,
        IntentState.SUBMISSION_UNKNOWN,
        IntentState.RECONCILED,
    },
    IntentState.SUBMISSION_UNKNOWN: {IntentState.RECONCILED},
    IntentState.ACKNOWLEDGED: set(),
    IntentState.REJECTED: set(),
    IntentState.CONFIRMED: set(),
    IntentState.RECONCILED: set(),
}


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise IntentJournalError("intent journal value is not canonical JSON") from exc


def _body_digest(body: Any) -> str:
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _record_hash(fields: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(fields).encode("utf-8")).hexdigest()


def _bounded_detail(detail: Optional[str]) -> Optional[str]:
    if detail is None:
        return None
    return str(detail)[:256]
