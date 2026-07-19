import pytest

from full_python.tradovate.errors import TradovateStateError
from full_python.tradovate.order_events import translate_user_sync_event


def _props(entity_type, event_type, entity):
    return {
        "e": "props",
        "d": {
            "entityType": entity_type,
            "eventType": event_type,
            "entity": entity,
        },
    }


def _translate(event):
    return translate_user_sync_event(event, account_id=456, contract_id=789)


def test_fill_created_maps_to_broker_fill_with_injected_scope():
    events = _translate(_props("fill", "Created", {
        "id": 5001,
        "orderId": 101,
        "contractId": 789,
        "timestamp": "2026-07-20T13:32:01Z",
        "action": "Buy",
        "qty": 1,
        "price": 20100.25,
        "active": False,
    }))

    assert len(events) == 1
    assert events[0].kind == "fill"
    assert events[0].data == {
        "orderId": 101,
        "action": "Buy",
        "qty": 1,
        "price": 20100.25,
        "timestamp": "2026-07-20T13:32:01Z",
        "reason": "",
        "accountId": 456,
        "contractId": 789,
    }


def test_order_canceled_and_expired_map_to_cancel():
    for status in ("Canceled", "Expired"):
        events = _translate(_props("order", "Updated", {
            "id": 102, "accountId": 456, "ordStatus": status,
        }))
        assert [(e.kind, e.data) for e in events] == [
            ("cancel", {"orderId": 102})
        ]


def test_order_rejected_maps_to_reject_with_reason():
    events = _translate(_props("order", "Updated", {
        "id": 103, "accountId": 456, "ordStatus": "Rejected",
        "text": "outside market hours",
    }))
    assert [(e.kind, e.data) for e in events] == [
        ("reject", {"orderId": 103, "reason": "outside market hours"})
    ]


def test_working_filled_and_transitional_statuses_are_silent():
    for status in ("Working", "Filled", "Completed", "PendingNew",
                   "PendingCancel", "PendingReplace", "Suspended"):
        events = _translate(_props("order", "Updated", {
            "id": 104, "ordStatus": status,
        }))
        assert events == []


def test_position_netpos_maps_to_side_and_qty():
    cases = [(1, "long", 1), (-2, "short", 2), (0, "flat", 0)]
    for net_pos, side, qty in cases:
        events = _translate(_props("position", "Updated", {
            "id": 701, "accountId": 456, "contractId": 789, "netPos": net_pos,
        }))
        assert [(e.kind, e.data) for e in events] == [
            ("position", {
                "accountId": 456, "contractId": 789, "side": side, "qty": qty,
            })
        ]


def test_non_lifecycle_entity_types_translate_to_nothing():
    for entity_type in ("cashBalance", "accountRiskStatus", "command",
                        "commandReport", "orderVersion", "account", "contract"):
        assert _translate(_props(entity_type, "Updated", {"id": 1})) == []


def test_foreign_identity_fails_closed():
    with pytest.raises(TradovateStateError, match="accountId"):
        _translate(_props("order", "Updated", {
            "id": 102, "accountId": 999, "ordStatus": "Canceled",
        }))
    with pytest.raises(TradovateStateError, match="contractId"):
        _translate(_props("fill", "Created", {
            "orderId": 101, "contractId": 111, "timestamp": "t",
            "action": "Buy", "qty": 1, "price": 1.0,
        }))


def test_malformed_fill_and_unknown_status_raise():
    with pytest.raises(TradovateStateError, match="missing qty"):
        _translate(_props("fill", "Created", {
            "orderId": 101, "timestamp": "t", "action": "Buy", "price": 1.0,
        }))
    with pytest.raises(TradovateStateError, match="unknown order status"):
        _translate(_props("order", "Updated", {
            "id": 102, "ordStatus": "Mystery",
        }))
    with pytest.raises(TradovateStateError, match="unexpected event type"):
        _translate(_props("fill", "Deleted", {
            "orderId": 101, "timestamp": "t", "action": "Buy",
            "qty": 1, "price": 1.0,
        }))
    with pytest.raises(TradovateStateError, match="unknown user-sync entity"):
        _translate(_props("mystery", "Updated", {"id": 1}))


def test_array_entities_translate_in_order():
    events = _translate(_props("order", "Updated", [
        {"id": 102, "ordStatus": "Canceled"},
        {"id": 103, "ordStatus": "Rejected", "text": "no"},
    ]))
    assert [e.kind for e in events] == ["cancel", "reject"]


def test_non_props_shutdown_and_malformed_events_raise():
    for event in (
        {"e": "shutdown", "d": {}},
        {"e": "chart", "d": {}},
        "not-a-dict",
        {"e": "props", "d": []},
        {"e": "props", "d": {"entityType": "order", "eventType": "Mystery",
                             "entity": {"id": 1}}},
        {"e": "props", "d": {"entityType": "order", "eventType": "Updated",
                             "entity": []}},
    ):
        with pytest.raises(TradovateStateError):
            _translate(event)
