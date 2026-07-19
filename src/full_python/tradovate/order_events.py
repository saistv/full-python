"""Translate user-sync property events into broker order-lifecycle events.

Pure translation, no I/O (Slice G2 of the order-pump design; audit P1-6).
The D2 user-sync stream is requested for exactly one account, so entities
that omit ``accountId``/``contractId`` inherit the configured scope; an
entity that carries a DIFFERENT identity raises (fail closed). Statuses and
entity shapes this module does not understand also raise — an unknown order
state on a live account is never safe to ignore.

Duplicate delivery is deliberately not handled here: the broker's own
lifecycle halts on duplicate fills and tolerates duplicate cancels.
"""
from __future__ import annotations

from typing import Any

from full_python.tradovate.broker import TradovateRawEvent
from full_python.tradovate.errors import TradovateStateError

# Order lifecycle is driven by fills and terminal order states. Working /
# Filled / Completed and the transitional Pending* statuses produce no raw
# event: acks are known at submission time and fills arrive as fill entities.
_SILENT_ORDER_STATUSES = frozenset({
    "Working",
    "Filled",
    "Completed",
    "PendingNew",
    "PendingCancel",
    "PendingReplace",
    "Suspended",
})
_CANCEL_ORDER_STATUSES = frozenset({"Canceled", "Expired"})

# Entity types that belong to the account cache, not the order lifecycle.
_NON_LIFECYCLE_ENTITY_TYPES = frozenset({
    "account",
    "cashBalance",
    "accountRiskStatus",
    "command",
    "commandReport",
    "orderVersion",
    "contract",
    "marginSnapshot",
    "user",
})


def translate_user_sync_event(
    event: Any, *, account_id: int, contract_id: int
) -> "list[TradovateRawEvent]":
    """Map one user-sync props event to zero or more broker raw events."""
    if not isinstance(event, dict) or event.get("e") != "props":
        raise TradovateStateError(
            "order-event translation requires a user-sync props event"
        )
    data = event.get("d")
    if not isinstance(data, dict):
        raise TradovateStateError("user-sync props data must be an object")
    entity_type = data.get("entityType")
    event_type = data.get("eventType")
    if event_type not in {"Created", "Updated", "Deleted"}:
        raise TradovateStateError(
            f"unknown user-sync event type {event_type!r}"
        )
    raw_entities = data.get("entity")
    if isinstance(raw_entities, dict):
        entities = [raw_entities]
    elif isinstance(raw_entities, list) and raw_entities:
        entities = raw_entities
    else:
        raise TradovateStateError(
            "user-sync props entity must be an object or nonempty array"
        )

    if entity_type in _NON_LIFECYCLE_ENTITY_TYPES:
        return []

    out: "list[TradovateRawEvent]" = []
    for entity in entities:
        if not isinstance(entity, dict):
            raise TradovateStateError(
                f"user-sync {entity_type} entity must be an object"
            )
        if entity_type == "fill":
            out.append(_fill_event(
                entity,
                event_type=event_type,
                account_id=account_id,
                contract_id=contract_id,
            ))
        elif entity_type == "order":
            raw = _order_event(
                entity, account_id=account_id, contract_id=contract_id
            )
            if raw is not None:
                out.append(raw)
        elif entity_type == "position":
            out.append(_position_event(
                entity, account_id=account_id, contract_id=contract_id
            ))
        else:
            raise TradovateStateError(
                f"unknown user-sync entity type {entity_type!r}"
            )
    return out


def _verify_identity(
    entity: "dict[str, Any]", *, account_id: int, contract_id: int, source: str
) -> None:
    for field, expected in (("accountId", account_id), ("contractId", contract_id)):
        value = entity.get(field)
        if value is not None and value != expected:
            raise TradovateStateError(
                f"{source} entity {field} {value!r} does not match the "
                f"configured {field} {expected!r}"
            )


def _required(entity: "dict[str, Any]", field: str, source: str) -> Any:
    value = entity.get(field)
    if value is None:
        raise TradovateStateError(f"{source} entity is missing {field}")
    return value


def _fill_event(
    entity: "dict[str, Any]", *, event_type: str, account_id: int, contract_id: int
) -> TradovateRawEvent:
    if event_type != "Created":
        # A mutated or deleted fill is account-history rewriting; never safe.
        raise TradovateStateError(
            f"fill entity arrived with unexpected event type {event_type!r}"
        )
    _verify_identity(
        entity, account_id=account_id, contract_id=contract_id, source="fill"
    )
    return TradovateRawEvent(kind="fill", data={
        "orderId": _required(entity, "orderId", "fill"),
        "action": _required(entity, "action", "fill"),
        "qty": _required(entity, "qty", "fill"),
        "price": _required(entity, "price", "fill"),
        "timestamp": _required(entity, "timestamp", "fill"),
        "reason": "",
        "accountId": account_id,
        "contractId": contract_id,
    })


def _order_event(
    entity: "dict[str, Any]", *, account_id: int, contract_id: int
) -> "TradovateRawEvent | None":
    _verify_identity(
        entity, account_id=account_id, contract_id=contract_id, source="order"
    )
    status = _required(entity, "ordStatus", "order")
    if status in _SILENT_ORDER_STATUSES:
        return None
    order_id = _required(entity, "id", "order")
    if status in _CANCEL_ORDER_STATUSES:
        return TradovateRawEvent(kind="cancel", data={"orderId": order_id})
    if status == "Rejected":
        return TradovateRawEvent(kind="reject", data={
            "orderId": order_id,
            "reason": str(entity.get("text") or entity.get("rejectReason") or ""),
        })
    raise TradovateStateError(
        f"unknown order status {status!r} for order {order_id!r}"
    )


def _position_event(
    entity: "dict[str, Any]", *, account_id: int, contract_id: int
) -> TradovateRawEvent:
    _verify_identity(
        entity, account_id=account_id, contract_id=contract_id, source="position"
    )
    net_pos = _required(entity, "netPos", "position")
    if isinstance(net_pos, bool) or not isinstance(net_pos, int):
        raise TradovateStateError("position entity netPos must be an integer")
    side = "long" if net_pos > 0 else "short" if net_pos < 0 else "flat"
    return TradovateRawEvent(kind="position", data={
        "accountId": account_id,
        "contractId": contract_id,
        "side": side,
        "qty": abs(net_pos),
    })
