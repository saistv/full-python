"""Fail-closed incremental Tradovate account synchronization."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
import math
import time
from typing import Any, Callable, Mapping, Optional, Sequence

from full_python.tradovate.account_sync import (
    ENTITY_TYPE_TO_COLLECTION,
    REQUIRED_SYNC_COLLECTIONS,
    TradovateAccountHydrator,
)
from full_python.tradovate.auth import TradovateToken
from full_python.tradovate.config import TradovateAdapterConfig
from full_python.tradovate.errors import TradovateStateError


class AccountEntityCache:
    """Entity-ID cache for one complete user-sync snapshot."""

    def __init__(self, entities: dict[str, dict[int, dict[str, Any]]]) -> None:
        self._entities = entities

    @classmethod
    def from_collections(
        cls,
        collections: Mapping[str, Sequence[dict[str, Any]]],
    ) -> "AccountEntityCache":
        entities: dict[str, dict[int, dict[str, Any]]] = {}
        for name in REQUIRED_SYNC_COLLECTIONS:
            if name not in collections:
                raise TradovateStateError(
                    f"account sync cache is missing required {name} collection"
                )
            rows = collections[name]
            if not isinstance(rows, (list, tuple)):
                raise TradovateStateError(f"{name} cache collection must be a sequence")
            indexed: dict[int, dict[str, Any]] = {}
            for row in rows:
                entity_id = _entity_id(row, name)
                if entity_id in indexed:
                    raise TradovateStateError(
                        f"duplicate {name} cache entity id {entity_id}"
                    )
                indexed[entity_id] = deepcopy(row)
            entities[name] = indexed
        return cls(entities)

    def collections(self) -> dict[str, tuple[dict[str, Any], ...]]:
        return {
            name: tuple(deepcopy(rows[entity_id]) for entity_id in sorted(rows))
            for name, rows in self._entities.items()
        }

    def apply_property_event(self, event: Any) -> None:
        if not isinstance(event, dict) or event.get("e") != "props":
            raise TradovateStateError("account sync event must be a props object")
        data = event.get("d")
        if not isinstance(data, dict):
            raise TradovateStateError("account sync props data must be an object")
        entity_type = data.get("entityType")
        if entity_type not in ENTITY_TYPE_TO_COLLECTION:
            raise TradovateStateError(
                f"unknown account sync entity type {entity_type!r}"
            )
        event_type = data.get("eventType")
        if event_type not in {"Created", "Updated", "Deleted"}:
            raise TradovateStateError(
                f"unknown account sync event type {event_type!r}"
            )
        raw_entities = data.get("entity")
        if isinstance(raw_entities, dict):
            event_entities = [raw_entities]
        elif isinstance(raw_entities, list) and raw_entities:
            event_entities = raw_entities
        else:
            raise TradovateStateError(
                "account sync props entity must be an object or nonempty array"
            )

        collection = ENTITY_TYPE_TO_COLLECTION[entity_type]
        draft = deepcopy(self._entities[collection])
        for entity in event_entities:
            entity_id = _entity_id(entity, collection)
            existing = draft.get(entity_id)
            if event_type == "Created":
                if existing is None:
                    draft[entity_id] = deepcopy(entity)
                elif existing != entity:
                    raise TradovateStateError(
                        f"conflicting Created event for {entity_type} id {entity_id}"
                    )
                continue
            if existing is None:
                raise TradovateStateError(
                    f"{event_type} event for unknown entity id {entity_id}"
                )
            if event_type == "Updated":
                draft[entity_id] = existing | deepcopy(entity)
            else:
                del draft[entity_id]
        self._entities[collection] = draft


class AccountRuntimeState(str, Enum):
    DISCONNECTED = "disconnected"
    RECOVERY_REQUIRED = "recovery_required"
    SYNCHRONIZED = "synchronized"


@dataclass(frozen=True)
class AccountRuntimeConnection:
    websocket: Any
    rest_client: Any


class TradovateAccountSyncRuntime:
    """Maintain one exact-account state cache and broker validity latch."""

    def __init__(
        self,
        config: TradovateAdapterConfig,
        *,
        broker: Any,
        auth_client: Any,
        token: TradovateToken,
        expected_trade_date: date,
        connection_factory: Callable[[TradovateToken], AccountRuntimeConnection],
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        heartbeat_interval_seconds: float = 2.5,
        liveness_timeout_seconds: float = 7.5,
        reconciliation_interval_seconds: float = 30.0,
    ) -> None:
        if type(expected_trade_date) is not date:
            raise TradovateStateError("account runtime expected_trade_date must be a date")
        for name, value in (
            ("heartbeat interval", heartbeat_interval_seconds),
            ("liveness timeout", liveness_timeout_seconds),
            ("reconciliation interval", reconciliation_interval_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) <= 0
            ):
                raise TradovateStateError(f"account runtime {name} must be positive")
        if liveness_timeout_seconds <= heartbeat_interval_seconds:
            raise TradovateStateError(
                "account runtime liveness timeout must exceed heartbeat interval"
            )
        self._config = config
        self._broker = broker
        self._auth = auth_client
        self._token = token
        self._expected_trade_date = expected_trade_date
        self._connection_factory = connection_factory
        self._clock = monotonic_clock
        self._utc_clock = utc_clock
        self._heartbeat_interval = float(heartbeat_interval_seconds)
        self._liveness_timeout = float(liveness_timeout_seconds)
        self._reconciliation_interval = float(reconciliation_interval_seconds)
        self._state = AccountRuntimeState.DISCONNECTED
        self._connection: Optional[AccountRuntimeConnection] = None
        self._hydrator: Optional[TradovateAccountHydrator] = None
        self._cache: Optional[AccountEntityCache] = None
        self._connected_at: Optional[float] = None
        self._last_heartbeat_sent: Optional[float] = None
        self._next_reconciliation: Optional[float] = None

    @property
    def state(self) -> AccountRuntimeState:
        return self._state

    @property
    def token(self) -> TradovateToken:
        return self._token

    def start(self) -> None:
        """Build fresh clients and re-establish complete account authority."""
        self._broker.invalidate_account_state("account sync connection start")
        self._state = AccountRuntimeState.RECOVERY_REQUIRED
        self._close_connection()
        try:
            self._install_connection(self._token)
        except Exception:
            self._fail("account sync startup failed")
            raise

    def run_once(self, *, max_wait_seconds: float) -> None:
        if (
            isinstance(max_wait_seconds, bool)
            or not isinstance(max_wait_seconds, (int, float))
            or not math.isfinite(float(max_wait_seconds))
            or max_wait_seconds < 0
        ):
            raise TradovateStateError("max_wait_seconds must be finite and nonnegative")
        if self._connection is None or self._state == AccountRuntimeState.DISCONNECTED:
            raise TradovateStateError("account sync runtime is not connected")
        try:
            if self._token.should_renew(
                self._utc_clock(),
                lead_seconds=self._config.token_renewal_lead_seconds,
            ):
                self._renew_and_replace()

            now = self._clock()
            if self._heartbeat_due(now):
                self._require_connection().websocket.send_heartbeat()
                self._last_heartbeat_sent = now

            wait_seconds = self._bounded_wait(now, float(max_wait_seconds))
            event = self._require_connection().websocket.receive_event(wait_seconds)
            now = self._clock()
            if event is not None:
                self._handle_event(event)
            self._require_live_connection(now)
            if self._reconciliation_due(now):
                self._reconcile()
        except Exception:
            self._fail("account sync runtime failure")
            raise

    def close(self) -> None:
        self._broker.invalidate_account_state("account sync runtime closed")
        self._cache = None
        self._hydrator = None
        self._state = AccountRuntimeState.DISCONNECTED
        self._close_connection()

    def _install_connection(self, token: TradovateToken) -> None:
        connection = self._connection_factory(token)
        if not isinstance(connection, AccountRuntimeConnection):
            raise TradovateStateError(
                "account connection factory must return AccountRuntimeConnection"
            )
        self._connection = connection
        connection.websocket.authorize(token.access_token)
        hydrator = TradovateAccountHydrator(
            self._config,
            user_id=token.user_id,
            expected_trade_date=self._expected_trade_date,
            websocket=connection.websocket,
            rest_client=connection.rest_client,
        )
        result = hydrator.hydrate_with_state()
        cache = AccountEntityCache.from_collections(result.collections)
        self._broker.hydrate_account_state(result.snapshot)
        now = self._clock()
        self._hydrator = hydrator
        self._cache = cache
        self._connected_at = now
        self._last_heartbeat_sent = now
        self._next_reconciliation = now + self._reconciliation_interval
        self._state = AccountRuntimeState.SYNCHRONIZED

    def _renew_and_replace(self) -> None:
        self._broker.invalidate_account_state("Tradovate token replacement")
        self._state = AccountRuntimeState.RECOVERY_REQUIRED
        self._close_connection()
        renewed = self._auth.renew_access_token(self._token)
        if not isinstance(renewed, TradovateToken):
            raise TradovateStateError("token renewal did not return a TradovateToken")
        if renewed.user_id != self._token.user_id:
            raise TradovateStateError("renewed token user identity changed")
        self._token = renewed
        self._install_connection(renewed)

    def _handle_event(self, event: Any) -> None:
        if not isinstance(event, dict):
            raise TradovateStateError("account websocket event must be an object")
        event_kind = event.get("e")
        if event_kind == "shutdown":
            reason = event.get("d")
            raise TradovateStateError(f"Tradovate account stream shutdown: {reason!r}")
        if event_kind != "props":
            raise TradovateStateError(
                f"unknown Tradovate account event kind {event_kind!r}"
            )
        self._broker.invalidate_account_state("user sync property update")
        self._state = AccountRuntimeState.RECOVERY_REQUIRED
        cache = self._require_cache()
        cache.apply_property_event(event)
        self._reconcile()

    def _reconcile(self) -> None:
        snapshot = self._require_hydrator().verify_sync_state(
            self._require_cache().collections()
        )
        self._broker.hydrate_account_state(snapshot)
        self._state = AccountRuntimeState.SYNCHRONIZED
        self._next_reconciliation = self._clock() + self._reconciliation_interval

    def _heartbeat_due(self, now: float) -> bool:
        return (
            self._last_heartbeat_sent is None
            or now - self._last_heartbeat_sent >= self._heartbeat_interval
        )

    def _bounded_wait(self, now: float, requested: float) -> float:
        deadlines = [requested]
        if self._last_heartbeat_sent is not None:
            deadlines.append(max(
                0.0,
                self._last_heartbeat_sent + self._heartbeat_interval - now,
            ))
        if self._next_reconciliation is not None:
            deadlines.append(max(0.0, self._next_reconciliation - now))
        return min(deadlines)

    def _reconciliation_due(self, now: float) -> bool:
        return (
            self._next_reconciliation is not None
            and now >= self._next_reconciliation
        )

    def _require_live_connection(self, now: float) -> None:
        connection = self._require_connection()
        activity = connection.websocket.last_transport_activity
        baseline = self._connected_at
        if activity is not None:
            try:
                activity_value = float(activity)
            except (TypeError, ValueError) as exc:
                raise TradovateStateError(
                    "Tradovate account stream activity timestamp is invalid"
                ) from exc
            if not math.isfinite(activity_value) or activity_value > now:
                raise TradovateStateError(
                    "Tradovate account stream activity timestamp is invalid"
                )
            baseline = (
                activity_value
                if baseline is None
                else max(activity_value, baseline)
            )
        if baseline is None or now - baseline > self._liveness_timeout:
            raise TradovateStateError("Tradovate account stream liveness deadline exceeded")

    def _fail(self, reason: str) -> None:
        self._broker.invalidate_account_state(reason)
        self._state = AccountRuntimeState.RECOVERY_REQUIRED
        try:
            self._close_connection()
        except Exception:
            # The original protocol/state failure is the actionable cause.
            # Connection ownership is already detached before close is called.
            pass

    def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.websocket.close()
        self._connected_at = None
        self._last_heartbeat_sent = None
        self._next_reconciliation = None

    def _require_connection(self) -> AccountRuntimeConnection:
        if self._connection is None:
            raise TradovateStateError("account sync connection is unavailable")
        return self._connection

    def _require_hydrator(self) -> TradovateAccountHydrator:
        if self._hydrator is None:
            raise TradovateStateError("account sync hydrator is unavailable")
        return self._hydrator

    def _require_cache(self) -> AccountEntityCache:
        if self._cache is None:
            raise TradovateStateError("account sync cache is unavailable")
        return self._cache


def _entity_id(entity: Any, collection: str) -> int:
    if not isinstance(entity, dict):
        raise TradovateStateError(f"{collection} cache contains a non-object entity")
    entity_id = entity.get("id")
    if type(entity_id) is not int or entity_id <= 0:
        raise TradovateStateError(f"{collection} entity has invalid id")
    return entity_id
