from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional, Protocol

from full_python.tradovate.errors import TradovateWebSocketError


@dataclass(frozen=True)
class WebSocketMessage:
    kind: str
    payload: Optional[Any] = None


class WebSocketTransport(Protocol):
    def send(self, frame: str) -> None:
        ...

    def receive(self, timeout_seconds: float) -> Optional[str]:
        ...

    def close(self) -> None:
        ...


def encode_request(endpoint: str, request_id: int, payload: Any) -> str:
    if isinstance(payload, str):
        body = payload
    else:
        body = json.dumps(payload, separators=(",", ":"))
    return f"{endpoint}\n{request_id}\n\n{body}"


def parse_message(frame: str) -> WebSocketMessage:
    normalized = frame.rstrip("\n")
    if normalized == "o":
        return WebSocketMessage(kind="open")
    if frame == "h":
        return WebSocketMessage(kind="heartbeat")
    if frame.startswith("a"):
        try:
            payload = json.loads(frame[1:])
        except json.JSONDecodeError as exc:
            raise TradovateWebSocketError("Invalid websocket array frame") from exc
        return WebSocketMessage(kind="array", payload=payload)
    if frame.startswith("c"):
        return WebSocketMessage(kind="close", payload=frame)
    raise TradovateWebSocketError("Unexpected websocket frame")


class TradovateWebSocketClient:
    def __init__(self, transport: WebSocketTransport, max_ignored_frames: int = 100) -> None:
        self.transport = transport
        self._request_id = 1
        self.max_ignored_frames = max_ignored_frames
        self._pending_events: Deque[dict] = deque()
        self._pending_responses: Deque[dict] = deque()

    def authorize(self, token: str) -> None:
        self.transport.send(encode_request("authorize", 0, token))
        response = self._next_response(0)
        self._raise_for_status(response, 0)

    def request(self, endpoint: str, payload: Any) -> Any:
        request_id = self._request_id
        self._request_id += 1
        self.transport.send(encode_request(endpoint, request_id, payload))
        response = self._next_response(request_id)
        self._raise_for_status(response, request_id)
        return response.get("d")

    def receive_event(self, timeout_seconds: float) -> Optional[dict]:
        if self._pending_events:
            return self._pending_events.popleft()

        ignored_frames = 0
        while True:
            ignored_frames += 1
            self._raise_if_too_many_ignored_frames(ignored_frames)
            frame = self.transport.receive(timeout_seconds)
            if frame is None:
                return None

            items = self._items_from_frame(frame, context="event")
            if items is None:
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "e" in item:
                    self._queue_items_after_match(items, item)
                    return item
                if "i" in item:
                    self._pending_responses.append(item)

    def close(self) -> None:
        self.transport.close()

    def _next_response(self, expected_id: int) -> dict:
        pending = self._pop_pending_response(expected_id)
        if pending is not None:
            return pending

        ignored_frames = 0
        while True:
            ignored_frames += 1
            self._raise_if_too_many_ignored_frames(ignored_frames)
            frame = self.transport.receive(30.0)
            if frame is None:
                raise TradovateWebSocketError(
                    f"Timed out waiting for websocket response id {expected_id}"
                )

            items = self._items_from_frame(frame, context="response")
            if items is None:
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("i") == expected_id:
                    self._queue_items_after_match(items, item)
                    return item
                if "i" in item:
                    self._pending_responses.append(item)
                if "e" in item:
                    self._pending_events.append(item)

    def _raise_for_status(self, response: dict, request_id: int) -> None:
        status = response.get("s")
        if status != 200:
            raise TradovateWebSocketError(
                f"Tradovate websocket request {request_id} failed with status {status}"
            )

    def _items_from_frame(self, frame: str, context: str) -> Optional[list]:
        message = parse_message(frame)
        if message.kind in ("open", "heartbeat"):
            return None
        if message.kind == "close":
            raise TradovateWebSocketError(f"Websocket closed while waiting for {context}")
        if message.kind != "array":
            raise TradovateWebSocketError(f"Unexpected websocket frame while waiting for {context}")
        if not isinstance(message.payload, list):
            raise TradovateWebSocketError("Websocket array payload must be a list")
        return message.payload

    def _pop_pending_response(self, expected_id: int) -> Optional[dict]:
        for _ in range(len(self._pending_responses)):
            item = self._pending_responses.popleft()
            if item.get("i") == expected_id:
                return item
            self._pending_responses.append(item)
        return None

    def _queue_items_after_match(self, items: list, matched_item: dict) -> None:
        matched = False
        for item in items:
            if item is matched_item:
                matched = True
                continue
            if not matched or not isinstance(item, dict):
                continue
            if "e" in item:
                self._pending_events.append(item)
            elif "i" in item:
                self._pending_responses.append(item)

    def _raise_if_too_many_ignored_frames(self, ignored_frames: int) -> None:
        if ignored_frames > self.max_ignored_frames:
            raise TradovateWebSocketError("Too many websocket frames without a matching message")
