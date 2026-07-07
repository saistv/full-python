from __future__ import annotations

from typing import List, Optional

import pytest

from full_python.tradovate.errors import TradovateWebSocketError
from full_python.tradovate.ws import (
    TradovateWebSocketClient,
    WebSocketMessage,
    encode_request,
    parse_message,
)


class FakeWebSocketTransport:
    def __init__(self, inbound_frames: List[Optional[str]]) -> None:
        self.inbound_frames = inbound_frames
        self.sent_frames: List[str] = []
        self.closed = False

    def send(self, frame: str) -> None:
        self.sent_frames.append(frame)

    def receive(self, timeout_seconds: float) -> Optional[str]:
        if not self.inbound_frames:
            return None
        return self.inbound_frames.pop(0)

    def close(self) -> None:
        self.closed = True


def test_encode_request_uses_compact_json_payload() -> None:
    assert (
        encode_request("md/getChart", 12, {"symbol": "NQZ5"})
        == 'md/getChart\n12\n\n{"symbol":"NQZ5"}'
    )


def test_parse_open_message() -> None:
    assert parse_message("o\n") == WebSocketMessage(kind="open")


def test_parse_response_array_message() -> None:
    message = parse_message('a[{"s":200,"i":3,"d":{"ok":true}}]')

    assert message.kind == "array"
    assert message.payload == [{"s": 200, "i": 3, "d": {"ok": True}}]


def test_parse_event_array_message() -> None:
    message = parse_message('a[{"e":"chart","d":{"charts":[]}}]')

    assert message.kind == "array"
    assert message.payload == [{"e": "chart", "d": {"charts": []}}]


def test_authorize_sends_token_frame_and_accepts_success_response() -> None:
    transport = FakeWebSocketTransport(['a[{"s":200,"i":0,"d":{}}]'])
    client = TradovateWebSocketClient(transport)

    client.authorize("token")

    assert transport.sent_frames == ["authorize\n0\n\ntoken"]


def test_request_sends_next_id_correlates_response_and_returns_payload() -> None:
    transport = FakeWebSocketTransport(
        ['a[{"s":200,"i":1,"d":{"historicalId":5,"realtimeId":6}}]']
    )
    client = TradovateWebSocketClient(transport)

    result = client.request("md/getChart", {"symbol": "NQZ5"})

    assert transport.sent_frames == ['md/getChart\n1\n\n{"symbol":"NQZ5"}']
    assert result == {"historicalId": 5, "realtimeId": 6}


def test_request_response_with_non_200_status_raises_error_with_status() -> None:
    transport = FakeWebSocketTransport(['a[{"s":404,"i":1,"d":{"error":"missing"}}]'])
    client = TradovateWebSocketClient(transport)

    with pytest.raises(TradovateWebSocketError, match="404"):
        client.request("md/getChart", {"symbol": "NQZ5"})


def test_receive_event_skips_heartbeat_and_returns_first_event_dict() -> None:
    transport = FakeWebSocketTransport(["h", 'a[{"e":"chart","d":{"charts":[]}}]'])
    client = TradovateWebSocketClient(transport)

    result = client.receive_event(timeout_seconds=1.0)

    assert result == {"e": "chart", "d": {"charts": []}}


def test_response_wait_preserves_unrelated_event_for_later_receive() -> None:
    transport = FakeWebSocketTransport(
        [
            'a[{"e":"chart","d":{"charts":[1]}}]',
            'a[{"s":200,"i":1,"d":{"historicalId":5}}]',
        ]
    )
    client = TradovateWebSocketClient(transport)

    assert client.request("md/getChart", {"symbol": "NQZ5"}) == {"historicalId": 5}
    assert client.receive_event(timeout_seconds=1.0) == {"e": "chart", "d": {"charts": [1]}}


def test_event_wait_preserves_unrelated_response_for_later_request() -> None:
    transport = FakeWebSocketTransport(
        [
            'a[{"s":200,"i":1,"d":{"historicalId":5}}]',
            'a[{"e":"chart","d":{"charts":[]}}]',
        ]
    )
    client = TradovateWebSocketClient(transport)

    assert client.receive_event(timeout_seconds=1.0) == {"e": "chart", "d": {"charts": []}}
    assert client.request("md/getChart", {"symbol": "NQZ5"}) == {"historicalId": 5}


def test_response_wait_preserves_later_items_in_same_array_frame() -> None:
    transport = FakeWebSocketTransport(
        [
            (
                'a[{"s":200,"i":1,"d":{"historicalId":5}},'
                '{"e":"chart","d":{"charts":[2]}},'
                '{"s":200,"i":2,"d":{"realtimeId":6}}]'
            )
        ]
    )
    client = TradovateWebSocketClient(transport)

    assert client.request("md/getChart", {"symbol": "NQZ5"}) == {"historicalId": 5}
    assert client.receive_event(timeout_seconds=1.0) == {"e": "chart", "d": {"charts": [2]}}
    assert client.request("md/getChart", {"symbol": "NQZ5"}) == {"realtimeId": 6}


def test_event_wait_preserves_later_items_in_same_array_frame() -> None:
    transport = FakeWebSocketTransport(
        [
            (
                'a[{"e":"chart","d":{"charts":[1]}},'
                '{"s":200,"i":1,"d":{"historicalId":5}},'
                '{"e":"chart","d":{"charts":[2]}}]'
            )
        ]
    )
    client = TradovateWebSocketClient(transport)

    assert client.receive_event(timeout_seconds=1.0) == {"e": "chart", "d": {"charts": [1]}}
    assert client.request("md/getChart", {"symbol": "NQZ5"}) == {"historicalId": 5}
    assert client.receive_event(timeout_seconds=1.0) == {"e": "chart", "d": {"charts": [2]}}


def test_receive_event_stops_after_too_many_ignored_frames() -> None:
    transport = FakeWebSocketTransport(["h"] * 101)
    client = TradovateWebSocketClient(transport, max_ignored_frames=100)

    with pytest.raises(TradovateWebSocketError, match="Too many websocket frames"):
        client.receive_event(timeout_seconds=1.0)


def test_response_wait_stops_after_too_many_ignored_frames() -> None:
    transport = FakeWebSocketTransport(["h"] * 101)
    client = TradovateWebSocketClient(transport, max_ignored_frames=100)

    with pytest.raises(TradovateWebSocketError, match="Too many websocket frames"):
        client.request("md/getChart", {"symbol": "NQZ5"})
