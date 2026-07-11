# Live Observe Gate (SP4 slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Gate 5 demo-observe stack: a real WebSocket transport, an observe-only live session runner, crash-safe event persistence, a post-session shadow-parity report, and a read-only account risk probe.

**Architecture:** One new low-level component (stdlib RFC 6455 client transport implementing the existing `WebSocketTransport` protocol) plus a new `full_python/live/` package that composes ONLY existing pieces (`TradovateWebSocketClient` → `TradovateMarketDataFeed` → `LiveBarSource` → `TradovateBroker(order_enabled=False)` → `LiveLoop`). A strategy wrapper records observe-mode signals to the ledger; the report replays the recorded bars through the identical no-fill strategy stack and diffs.

**Tech Stack:** Python 3.9-compatible stdlib only (`socket`, `ssl`, `hashlib`, `base64`, `argparse`, `logging`). pytest with fake sockets/clients.

**Spec:** `docs/superpowers/specs/2026-07-11-live-observe-gate-design.md`.

## Global Constraints

- Branch: `claude/m4-regime`. Repo: `/Users/sais/Documents/New Beginning/full-python`. Never commit red tests.
- Python 3.9 compatible: `from __future__ import annotations` at top of every module; no `X | Y` outside annotations; no `Z`-suffix `fromisoformat`.
- Stdlib only. No new dependencies.
- NO changes to `LiveLoop`, `PositionEngine`, `OrderStateMachine`, `RiskSupervisor`, `TradovateBroker`, strategy code, or production config values.
- Observe mode is pinned: `order_enabled=False` / `flatten_enabled=False` are literals in `observe_adapter_config()`; no CLI flag, env var, or parameter may exist to change them. Demo environment is hardcoded.
- No silent reconnects. Failures surface with reason; the runner's only recovery is clean shutdown + report.
- Credentials never logged. Probe output passes through the existing `_redact`.
- Run `python3 -m pytest -q` at the end of every task. Baseline before Task 1: **294 passed, 3 skipped**.

## Deviations from the spec (decided at plan time)

1. The probe uses `cashBalance/list` (GET) instead of the spec's
   `cashBalance/getcashbalancesnapshot` (which is a POST on Tradovate's
   API). The spec's GET-only rule outranks its endpoint list; Task 8
   amends the spec line.
2. The spec's "reusing the existing report rendering helpers": the
   helpers in `reporting/html_report.py` are all private and shaped for
   full backtest reports; the session report writes its own compact
   self-contained HTML instead (same visual conventions, ~60 lines).

---

### Task 1: RFC 6455 frame codec + handshake (pure functions)

**Files:**
- Create: `src/full_python/tradovate/transport.py`
- Test: `tests/test_tradovate_transport.py` (new)

**Interfaces:**
- Consumes: `TradovateWebSocketError` from `full_python.tradovate.errors`.
- Produces (used by Task 2): `websocket_key() -> str`,
  `expected_accept(key: str) -> str`,
  `build_handshake_request(host: str, path: str, key: str) -> bytes`,
  `validate_handshake_response(response: bytes, key: str) -> None`,
  `encode_frame(opcode: int, payload: bytes, mask_key: bytes) -> bytes`,
  `encode_text_frame(payload: str, mask_key: bytes) -> bytes`,
  `read_frame(read_exact: Callable[[int], bytes]) -> Tuple[int, bool, bytes]`
  (returns `(opcode, fin, payload)`), and opcode constants
  `OPCODE_CONTINUATION/TEXT/BINARY/CLOSE/PING/PONG`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tradovate_transport.py`:

```python
from __future__ import annotations

import pytest

from full_python.tradovate.errors import TradovateWebSocketError
from full_python.tradovate.transport import (
    OPCODE_BINARY,
    OPCODE_CLOSE,
    OPCODE_PING,
    OPCODE_TEXT,
    build_handshake_request,
    encode_frame,
    encode_text_frame,
    expected_accept,
    read_frame,
    validate_handshake_response,
    websocket_key,
)

# RFC 6455 section 1.3 worked example
_RFC_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
_RFC_ACCEPT = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
# RFC 6455 section 5.7: single-frame masked text "Hello"
_RFC_MASKED_HELLO = bytes(
    [0x81, 0x85, 0x37, 0xFA, 0x21, 0x3D, 0x7F, 0x9F, 0x4D, 0x51, 0x58]
)


def _reader(data: bytes):
    state = {"offset": 0}

    def read_exact(count: int) -> bytes:
        start = state["offset"]
        assert start + count <= len(data), "test frame truncated"
        state["offset"] = start + count
        return data[start:start + count]

    return read_exact


def test_expected_accept_matches_rfc_worked_example() -> None:
    assert expected_accept(_RFC_KEY) == _RFC_ACCEPT


def test_websocket_key_is_16_random_bytes_base64() -> None:
    import base64

    key = websocket_key()
    assert len(base64.b64decode(key)) == 16
    assert websocket_key() != key  # random per call


def test_handshake_request_contains_upgrade_headers() -> None:
    request = build_handshake_request("md-d.tradovateapi.com", "/v1/websocket", _RFC_KEY)
    text = request.decode("ascii")
    assert text.startswith("GET /v1/websocket HTTP/1.1\r\n")
    assert "Host: md-d.tradovateapi.com\r\n" in text
    assert "Upgrade: websocket\r\n" in text
    assert "Connection: Upgrade\r\n" in text
    assert f"Sec-WebSocket-Key: {_RFC_KEY}\r\n" in text
    assert "Sec-WebSocket-Version: 13\r\n" in text
    assert text.endswith("\r\n\r\n")


def test_validate_handshake_accepts_matching_accept_header() -> None:
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {_RFC_ACCEPT}\r\n"
        "\r\n"
    ).encode("ascii")
    validate_handshake_response(response, _RFC_KEY)  # no exception


def test_validate_handshake_rejects_non_101_status() -> None:
    response = b"HTTP/1.1 403 Forbidden\r\n\r\n"
    with pytest.raises(TradovateWebSocketError, match="403"):
        validate_handshake_response(response, _RFC_KEY)


def test_validate_handshake_rejects_wrong_accept() -> None:
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Accept: bogus\r\n\r\n"
    ).encode("ascii")
    with pytest.raises(TradovateWebSocketError, match="Accept"):
        validate_handshake_response(response, _RFC_KEY)


def test_encode_text_frame_matches_rfc_masked_hello() -> None:
    frame = encode_text_frame("Hello", mask_key=bytes([0x37, 0xFA, 0x21, 0x3D]))
    assert frame == _RFC_MASKED_HELLO


def test_encode_frame_16_bit_and_64_bit_lengths() -> None:
    mask = b"\x00\x00\x00\x00"  # zero mask leaves payload readable
    medium = encode_frame(OPCODE_TEXT, b"a" * 300, mask)
    assert medium[1] == 0x80 | 126
    assert int.from_bytes(medium[2:4], "big") == 300
    large = encode_frame(OPCODE_TEXT, b"a" * 70000, mask)
    assert large[1] == 0x80 | 127
    assert int.from_bytes(large[2:10], "big") == 70000


def test_read_frame_unmasked_server_text() -> None:
    opcode, fin, payload = read_frame(_reader(b"\x81\x05Hello"))
    assert (opcode, fin, payload) == (OPCODE_TEXT, True, b"Hello")


def test_read_frame_unmasks_masked_payload() -> None:
    opcode, fin, payload = read_frame(_reader(_RFC_MASKED_HELLO))
    assert (opcode, fin, payload) == (OPCODE_TEXT, True, b"Hello")


def test_read_frame_16_bit_length() -> None:
    data = b"\x81\x7e" + (300).to_bytes(2, "big") + b"b" * 300
    opcode, fin, payload = read_frame(_reader(data))
    assert payload == b"b" * 300


def test_read_frame_fragmented_and_control_opcodes() -> None:
    first = read_frame(_reader(b"\x01\x03Hel"))     # text, FIN=0
    cont = read_frame(_reader(b"\x80\x02lo"))       # continuation, FIN=1
    ping = read_frame(_reader(b"\x89\x02hi"))
    close = read_frame(_reader(b"\x88\x00"))
    assert first == (OPCODE_TEXT, False, b"Hel")
    assert cont == (0x0, True, b"lo")
    assert ping == (OPCODE_PING, True, b"hi")
    assert close == (OPCODE_CLOSE, True, b"")


def test_read_frame_rejects_rsv_bits() -> None:
    with pytest.raises(TradovateWebSocketError, match="RSV"):
        read_frame(_reader(b"\xc1\x05Hello"))


def test_read_frame_rejects_oversized_payload() -> None:
    header = b"\x81\x7f" + (17 * 1024 * 1024).to_bytes(8, "big")
    with pytest.raises(TradovateWebSocketError, match="too large"):
        read_frame(_reader(header))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tradovate_transport.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'full_python.tradovate.transport'`.

- [ ] **Step 3: Implement the codec in `src/full_python/tradovate/transport.py`**

```python
"""Real WebSocket transport for the Tradovate adapter.

Stdlib-only RFC 6455 client over socket+ssl, implementing the
WebSocketTransport protocol from full_python.tradovate.ws, so the
framing client, feed, and every existing fake-transport test are
untouched. Design rules (Gate 5 spec): every receive is bounded by the
caller's timeout; Tradovate's SockJS-style "o" open and "h" heartbeat
frames are consumed here (heartbeats answered with "[]") and never
surface to the framing layer; a server close handshake surfaces as the
"c" frame the framing layer already maps to "closed"; binary frames are
unexpected -> error, never guess.
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import time
from typing import Callable, Optional, Tuple
from urllib.parse import urlsplit

from full_python.tradovate.errors import TradovateWebSocketError

_WS_ACCEPT_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_PAYLOAD_BYTES = 16 * 1024 * 1024

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


def websocket_key() -> str:
    return base64.b64encode(os.urandom(16)).decode("ascii")


def expected_accept(key: str) -> str:
    digest = hashlib.sha1((key + _WS_ACCEPT_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def build_handshake_request(host: str, path: str, key: str) -> bytes:
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def validate_handshake_response(response: bytes, key: str) -> None:
    head = response.decode("latin-1")
    lines = head.split("\r\n")
    status_parts = lines[0].split(" ", 2)
    if len(status_parts) < 2 or status_parts[1] != "101":
        raise TradovateWebSocketError(f"Websocket handshake rejected: {lines[0]!r}")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    if headers.get("upgrade", "").lower() != "websocket":
        raise TradovateWebSocketError("Websocket handshake missing Upgrade header")
    if headers.get("sec-websocket-accept") != expected_accept(key):
        raise TradovateWebSocketError("Sec-WebSocket-Accept mismatch")


def encode_frame(opcode: int, payload: bytes, mask_key: bytes) -> bytes:
    if len(mask_key) != 4:
        raise TradovateWebSocketError("mask_key must be 4 bytes")
    header = bytearray([0x80 | opcode])  # FIN + opcode
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header += length.to_bytes(2, "big")
    else:
        header.append(0x80 | 127)
        header += length.to_bytes(8, "big")
    header += mask_key
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return bytes(header) + masked


def encode_text_frame(payload: str, mask_key: bytes) -> bytes:
    return encode_frame(OPCODE_TEXT, payload.encode("utf-8"), mask_key)


def read_frame(read_exact: Callable[[int], bytes]) -> Tuple[int, bool, bytes]:
    first, second = read_exact(2)
    if first & 0x70:
        raise TradovateWebSocketError("Unsupported RSV bits in websocket frame")
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = int.from_bytes(read_exact(2), "big")
    elif length == 127:
        length = int.from_bytes(read_exact(8), "big")
    if length > _MAX_PAYLOAD_BYTES:
        raise TradovateWebSocketError(f"websocket frame too large: {length} bytes")
    mask = read_exact(4) if masked else b""
    payload = read_exact(length) if length else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, fin, payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tradovate_transport.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q` — expected 294 + new passed, 3 skipped.

```bash
git add src/full_python/tradovate/transport.py tests/test_tradovate_transport.py
git commit -m "feat: RFC 6455 frame codec and handshake for the Tradovate transport"
```

---

### Task 2: `WebSocketConnection` + `connect_websocket`

**Files:**
- Modify: `src/full_python/tradovate/transport.py` (append)
- Test: `tests/test_tradovate_transport.py` (append)

**Interfaces:**
- Consumes: Task 1's codec functions.
- Produces (used by Task 7):
  `WebSocketConnection(sock, prebuffer: bytes = b"", monotonic_clock=time.monotonic)`
  implementing the `WebSocketTransport` protocol
  (`send(frame: str) -> None`, `receive(timeout_seconds: float) -> Optional[str]`,
  `close() -> None`);
  `connect_websocket(url: str, timeout_seconds: float = 15.0, socket_factory=None) -> WebSocketConnection`.
  `socket_factory` is `Callable[[str, int, float], socket-like]`; the
  default builds a TLS socket. The socket-like needs `sendall`, `recv`,
  `settimeout`, `close`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tradovate_transport.py`:

```python
import socket as socket_module

from full_python.tradovate.transport import WebSocketConnection, connect_websocket


class FakeSocket:
    """Scripted socket: recv pops byte-chunks; sendall records."""

    def __init__(self, chunks=None):
        self.chunks = list(chunks or [])
        self.sent = b""
        self.timeouts = []
        self.closed = False

    def recv(self, size):
        if not self.chunks:
            raise socket_module.timeout("no more scripted data")
        head = self.chunks[0]
        if isinstance(head, Exception):
            self.chunks.pop(0)
            raise head
        take, rest = head[:size], head[size:]
        if rest:
            self.chunks[0] = rest
        else:
            self.chunks.pop(0)
        return take

    def sendall(self, data):
        self.sent += data

    def settimeout(self, value):
        self.timeouts.append(value)

    def close(self):
        self.closed = True


def _server_text(payload: str) -> bytes:
    """Unmasked server text frame (servers never mask)."""
    body = payload.encode("utf-8")
    assert len(body) < 126
    return bytes([0x81, len(body)]) + body


def _decode_all_client_frames(data: bytes):
    """Parse the masked frames a client sent, as (opcode, payload) pairs."""
    state = {"offset": 0}

    def read_exact(count: int) -> bytes:
        start = state["offset"]
        assert start + count <= len(data)
        state["offset"] = start + count
        return data[start:start + count]

    frames = []
    while state["offset"] < len(data):
        opcode, _fin, payload = read_frame(read_exact)
        frames.append((opcode, payload))
    return frames


def test_receive_returns_server_text_frame() -> None:
    conn = WebSocketConnection(FakeSocket([_server_text('a[{"s":200,"i":0}]')]))
    assert conn.receive(5.0) == 'a[{"s":200,"i":0}]'


def test_receive_skips_sockjs_open_and_answers_heartbeat() -> None:
    conn = WebSocketConnection(FakeSocket([
        _server_text("o"),
        _server_text("h"),
        _server_text('a[{"e":"chart","d":{}}]'),
    ]))

    assert conn.receive(5.0) == 'a[{"e":"chart","d":{}}]'
    frames = _decode_all_client_frames(conn._sock.sent)
    assert frames == [(OPCODE_TEXT, b"[]")]  # heartbeat answered, hidden


def test_receive_answers_ping_with_pong_same_payload() -> None:
    ping = bytes([0x89, 0x02]) + b"hi"
    conn = WebSocketConnection(FakeSocket([ping, _server_text("a[]")]))

    assert conn.receive(5.0) == "a[]"
    frames = _decode_all_client_frames(conn._sock.sent)
    assert frames == [(0xA, b"hi")]


def test_receive_reassembles_fragmented_text() -> None:
    fragments = bytes([0x01, 0x03]) + b"a[1" + bytes([0x80, 0x01]) + b"]"
    conn = WebSocketConnection(FakeSocket([fragments]))
    assert conn.receive(5.0) == "a[1]"


def test_receive_timeout_returns_none() -> None:
    conn = WebSocketConnection(FakeSocket([]))  # recv raises timeout
    assert conn.receive(0.05) is None


def test_server_close_yields_c_then_raises() -> None:
    close_frame = bytes([0x88, 0x00])
    conn = WebSocketConnection(FakeSocket([close_frame]))

    assert conn.receive(5.0) == "c"
    frames = _decode_all_client_frames(conn._sock.sent)
    assert frames == [(OPCODE_CLOSE, b"")]  # close handshake completed
    with pytest.raises(TradovateWebSocketError, match="closed"):
        conn.receive(5.0)


def test_binary_frame_raises() -> None:
    binary = bytes([0x82, 0x01, 0x00])
    conn = WebSocketConnection(FakeSocket([binary]))
    with pytest.raises(TradovateWebSocketError, match="binary"):
        conn.receive(5.0)


def test_connection_drop_mid_frame_raises() -> None:
    # one header byte arrives, then the peer closes (recv -> b"")
    conn = WebSocketConnection(FakeSocket([b"\x81", b""]))
    with pytest.raises(TradovateWebSocketError, match="dropped"):
        conn.receive(5.0)


def test_prebuffer_bytes_are_consumed_before_socket() -> None:
    conn = WebSocketConnection(FakeSocket([]), prebuffer=_server_text("a[7]"))
    assert conn.receive(5.0) == "a[7]"


def test_send_writes_masked_text_frame() -> None:
    sock = FakeSocket([])
    conn = WebSocketConnection(sock)
    conn.send('[{"url":"authorize"}]')
    frames = _decode_all_client_frames(sock.sent)
    assert frames == [(OPCODE_TEXT, b'[{"url":"authorize"}]')]
    assert sock.sent[1] & 0x80  # mask bit set on the wire


def test_connect_websocket_performs_handshake_and_keeps_leftover() -> None:
    captured = {}

    class HandshakeSocket(FakeSocket):
        def sendall(self, data):
            super().sendall(data)
            if b"Sec-WebSocket-Key" in data and not self.chunks:
                text = data.decode("ascii")
                key = [
                    line.split(": ", 1)[1]
                    for line in text.split("\r\n")
                    if line.startswith("Sec-WebSocket-Key: ")
                ][0]
                captured["key"] = key
                response = (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    f"Sec-WebSocket-Accept: {expected_accept(key)}\r\n"
                    "\r\n"
                ).encode("ascii")
                # server sends the SockJS open frame in the same packet
                self.chunks.append(response + _server_text("o") + _server_text("a[5]"))

    sock = HandshakeSocket()

    def factory(host, port, timeout_seconds):
        captured["target"] = (host, port, timeout_seconds)
        return sock

    conn = connect_websocket(
        "wss://md-d.tradovateapi.com/v1/websocket", 9.0, socket_factory=factory
    )

    assert captured["target"] == ("md-d.tradovateapi.com", 443, 9.0)
    assert conn.receive(5.0) == "a[5]"  # "o" consumed; leftover bytes preserved


def test_connect_websocket_rejects_non_wss() -> None:
    with pytest.raises(TradovateWebSocketError, match="scheme"):
        connect_websocket("ws://insecure.example/ws", socket_factory=lambda *a: FakeSocket([]))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tradovate_transport.py -q`
Expected: new tests fail with `ImportError: cannot import name 'WebSocketConnection'`.

- [ ] **Step 3: Implement (append to `transport.py`)**

```python
class _ReadProgress:
    def __init__(self) -> None:
        self.consumed = False


class WebSocketConnection:
    """Implements the WebSocketTransport protocol over a connected socket.

    `prebuffer` holds any bytes the handshake read past the header
    terminator (the server's first frames can share a TCP segment with
    the 101 response); they are consumed before the socket is touched.
    """

    def __init__(self, sock, prebuffer: bytes = b"", monotonic_clock=time.monotonic) -> None:
        self._sock = sock
        self._buffer = bytearray(prebuffer)
        self._monotonic = monotonic_clock
        self._closed = False

    def send(self, frame: str) -> None:
        if self._closed:
            raise TradovateWebSocketError("websocket closed")
        self._sock.sendall(encode_text_frame(frame, os.urandom(4)))

    def receive(self, timeout_seconds: float) -> Optional[str]:
        if self._closed:
            raise TradovateWebSocketError("websocket closed")
        deadline = self._monotonic() + timeout_seconds
        fragments: list = []
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                if fragments:
                    raise TradovateWebSocketError("timeout inside fragmented websocket message")
                return None
            self._sock.settimeout(max(remaining, 0.001))
            progress = _ReadProgress()
            try:
                opcode, fin, payload = read_frame(
                    lambda count: self._read_exact(count, progress)
                )
            except socket.timeout:
                if progress.consumed or fragments:
                    raise TradovateWebSocketError("timeout mid-frame on websocket")
                return None
            if opcode == OPCODE_PING:
                self._sock.sendall(encode_frame(OPCODE_PONG, payload, os.urandom(4)))
                continue
            if opcode == OPCODE_PONG:
                continue
            if opcode == OPCODE_CLOSE:
                try:
                    self._sock.sendall(encode_frame(OPCODE_CLOSE, b"", os.urandom(4)))
                except OSError:
                    pass
                self._closed = True
                # The framing layer already maps the SockJS "c" frame to
                # "closed" errors; surface the WS-level close the same way.
                return "c"
            if opcode == OPCODE_BINARY:
                raise TradovateWebSocketError("unexpected binary websocket frame")
            if opcode == OPCODE_CONTINUATION and not fragments:
                raise TradovateWebSocketError("continuation frame without a message start")
            if opcode == OPCODE_TEXT and fragments:
                raise TradovateWebSocketError("interleaved websocket text frames")
            fragments.append(payload)
            if not fin:
                continue
            text = b"".join(fragments).decode("utf-8")
            fragments = []
            if text == "o":
                continue  # SockJS open notice: carries nothing
            if text == "h":
                self.send("[]")  # answer the server heartbeat, stay hidden
                continue
            return text

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.sendall(encode_frame(OPCODE_CLOSE, b"", os.urandom(4)))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def _read_exact(self, count: int, progress: _ReadProgress) -> bytes:
        result = bytearray()
        while len(result) < count:
            if self._buffer:
                take = min(count - len(result), len(self._buffer))
                result += self._buffer[:take]
                del self._buffer[:take]
                progress.consumed = True
                continue
            chunk = self._sock.recv(count - len(result))
            if chunk == b"":
                self._closed = True
                raise TradovateWebSocketError("websocket connection dropped mid-frame")
            progress.consumed = True
            result += chunk
        return bytes(result)


def connect_websocket(
    url: str,
    timeout_seconds: float = 15.0,
    socket_factory=None,
) -> WebSocketConnection:
    parts = urlsplit(url)
    if parts.scheme != "wss":
        raise TradovateWebSocketError(f"unsupported websocket scheme: {parts.scheme!r}")
    host = parts.hostname or ""
    port = parts.port or 443
    path = parts.path or "/"
    if socket_factory is None:
        socket_factory = _tls_socket
    sock = socket_factory(host, port, timeout_seconds)
    key = websocket_key()
    sock.sendall(build_handshake_request(host, path, key))
    header, leftover = _read_handshake(sock)
    validate_handshake_response(header, key)
    return WebSocketConnection(sock, prebuffer=leftover)


def _tls_socket(host: str, port: int, timeout_seconds: float):
    raw = socket.create_connection((host, port), timeout=timeout_seconds)
    context = ssl.create_default_context()
    return context.wrap_socket(raw, server_hostname=host)


def _read_handshake(sock) -> Tuple[bytes, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1024)
        if chunk == b"":
            raise TradovateWebSocketError("connection closed during websocket handshake")
        data += chunk
        if len(data) > 65536:
            raise TradovateWebSocketError("oversized websocket handshake response")
    split_at = data.index(b"\r\n\r\n") + 4
    return bytes(data[:split_at]), bytes(data[split_at:])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tradovate_transport.py -q` — expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q`

```bash
git add src/full_python/tradovate/transport.py tests/test_tradovate_transport.py
git commit -m "feat: real websocket connection and TLS connect for Tradovate transport"
```

---

### Task 3: `PersistentEventLedger`

**Files:**
- Create: `src/full_python/live/__init__.py` (empty)
- Create: `src/full_python/live/persistence.py`
- Test: `tests/test_live_persistence.py` (new)

**Interfaces:**
- Consumes: `EventLedger`, `EventRecord`, `EventType` from `full_python.events`.
- Produces (used by Task 7): `PersistentEventLedger(path)` — an
  `EventLedger` subclass whose `append(...)` writes + flushes each record
  to `path` as JSONL immediately; `close() -> None`. File format identical
  to `EventLedger.write_jsonl`, readable by `EventLedger.read_jsonl`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_persistence.py`:

```python
from __future__ import annotations

from full_python.events import EventLedger, EventType
from full_python.live.persistence import PersistentEventLedger


def test_each_append_is_on_disk_immediately_without_close(tmp_path) -> None:
    path = tmp_path / "session" / "events.jsonl"
    ledger = PersistentEventLedger(path)

    ledger.append(EventType.BAR, timestamp_utc="2026-07-11T13:31:00Z",
                  payload={"close": 1.0})
    ledger.append(EventType.ORDER_INTENT, timestamp_utc="2026-07-11T13:32:00Z",
                  payload={"side": "buy"})
    # no close(): simulate a crash by reading the file right now
    loaded = EventLedger.read_jsonl(path)

    assert [r.to_dict() for r in loaded.records] == [r.to_dict() for r in ledger.records]
    assert loaded.records[1].payload == {"side": "buy"}


def test_behaves_as_a_normal_event_ledger_in_memory(tmp_path) -> None:
    ledger = PersistentEventLedger(tmp_path / "events.jsonl")
    record = ledger.append(EventType.BAR, timestamp_utc="2026-07-11T13:31:00Z")
    assert ledger.records == [record]
    assert record.event_id == "evt-00000001"
    ledger.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_live_persistence.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'full_python.live'`.

- [ ] **Step 3: Implement**

Create empty `src/full_python/live/__init__.py`, then
`src/full_python/live/persistence.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_live_persistence.py -q` — expected: 2 passed.

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q`

```bash
git add src/full_python/live/__init__.py src/full_python/live/persistence.py tests/test_live_persistence.py
git commit -m "feat: crash-safe persistent event ledger for live sessions"
```

---

### Task 4: `RecordingStrategy`

**Files:**
- Create: `src/full_python/live/recording.py`
- Test: `tests/test_live_recording.py` (new)

**Interfaces:**
- Consumes: `EventLedger`, `EventType`; `StrategyResult`/`OrderIntent`/`ExitDecision` from `full_python.models`.
- Produces (used by Tasks 5, 7): `RecordingStrategy(inner, ledger)` with
  `on_bar_context(*, session_pnl: float, daily_limit_hit: bool) -> None`
  (forwards to inner if inner has it) and
  `on_bar(bar: MarketBar) -> StrategyResult` (returns inner's result
  unchanged; appends one `EventType.ORDER_INTENT` per intent with payload
  keys `symbol, side, quantity, reason, stop_price`, and one
  `EventType.EXIT` per exit with `exit_decision.to_payload()`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_recording.py`:

```python
from __future__ import annotations

from full_python.events import EventLedger, EventType
from full_python.live.recording import RecordingStrategy
from full_python.models import ExitDecision, MarketBar, OrderIntent, StrategyResult


def _bar(ts: str = "2026-07-11T13:31:00Z") -> MarketBar:
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=100.0, high=101.0,
                     low=99.0, close=100.5, volume=10.0)


class ScriptedInner:
    def __init__(self, result: StrategyResult) -> None:
        self._result = result
        self.contexts = []

    def on_bar_context(self, *, session_pnl: float, daily_limit_hit: bool) -> None:
        self.contexts.append((session_pnl, daily_limit_hit))

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        return self._result


def test_records_intents_and_exits_and_returns_result_unchanged() -> None:
    bar = _bar()
    result = StrategyResult(
        order_intents=(OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc, symbol="NQ", side="buy", quantity=2,
            reason="adaptive_trend", metadata={"stop_price": 95.5},
        ),),
        exits=(ExitDecision(timestamp_utc=bar.timestamp_utc, symbol="NQ",
                            reason="atf_flip"),),
    )
    ledger = EventLedger()
    strategy = RecordingStrategy(ScriptedInner(result), ledger)

    returned = strategy.on_bar(bar)

    assert returned is result
    kinds = [r.event_type for r in ledger.records]
    assert kinds == [EventType.ORDER_INTENT, EventType.EXIT]
    assert ledger.records[0].payload == {
        "symbol": "NQ", "side": "buy", "quantity": 2,
        "reason": "adaptive_trend", "stop_price": 95.5,
    }
    assert ledger.records[0].timestamp_utc == bar.timestamp_utc
    assert ledger.records[1].payload["reason"] == "atf_flip"


def test_forwards_context_and_tolerates_inner_without_hook() -> None:
    inner = ScriptedInner(StrategyResult())
    strategy = RecordingStrategy(inner, EventLedger())
    strategy.on_bar_context(session_pnl=-42.0, daily_limit_hit=True)
    assert inner.contexts == [(-42.0, True)]

    class Bare:
        def on_bar(self, bar):
            return StrategyResult()

    bare = RecordingStrategy(Bare(), EventLedger())
    bare.on_bar_context(session_pnl=0.0, daily_limit_hit=False)  # no AttributeError
    assert bare.on_bar(_bar()) == StrategyResult()


def test_quiet_bar_records_nothing() -> None:
    ledger = EventLedger()
    strategy = RecordingStrategy(ScriptedInner(StrategyResult()), ledger)
    strategy.on_bar(_bar())
    assert ledger.records == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_live_recording.py -q`
Expected: FAIL — no module `full_python.live.recording`.

- [ ] **Step 3: Implement `src/full_python/live/recording.py`**

```python
"""Strategy wrapper that records observe-mode signals to the ledger.

With orders disabled the broker never fills, so the strategy's raw
on_bar output IS the observe-mode signal stream. LiveLoop does not
ledger intents itself (in the sim that is PositionEngine's job), so
this wrapper writes ORDER_INTENT / EXIT events -- the record the
shadow report (session_report.py) diffs against replay.

LiveLoop can suppress intents after a supervisor breach
(entries_allowed); this wrapper records the PRE-suppression stream. In
observe mode the supervisor has no daily_loss_stop, so the streams are
identical by construction.
"""
from __future__ import annotations

import logging

from full_python.events import EventLedger, EventType
from full_python.models import MarketBar, StrategyResult

logger = logging.getLogger("full_python.live")


class RecordingStrategy:
    def __init__(self, inner, ledger: EventLedger) -> None:
        self._inner = inner
        self._ledger = ledger
        self._session_pnl = 0.0
        self._daily_limit_hit = False

    def on_bar_context(self, *, session_pnl: float, daily_limit_hit: bool) -> None:
        self._session_pnl = session_pnl
        self._daily_limit_hit = daily_limit_hit
        inner_hook = getattr(self._inner, "on_bar_context", None)
        if inner_hook is not None:
            inner_hook(session_pnl=session_pnl, daily_limit_hit=daily_limit_hit)

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        result = self._inner.on_bar(bar)
        for intent in result.order_intents:
            payload = {
                "symbol": intent.symbol,
                "side": intent.side,
                "quantity": intent.quantity,
                "reason": intent.reason,
                "stop_price": intent.metadata.get("stop_price"),
            }
            self._ledger.append(
                EventType.ORDER_INTENT, timestamp_utc=bar.timestamp_utc, payload=payload
            )
            logger.info(
                "SIGNAL %s %s %dx stop=%s",
                bar.timestamp_utc, intent.side, intent.quantity, payload["stop_price"],
            )
        for exit_decision in result.exits:
            self._ledger.append(
                EventType.EXIT,
                timestamp_utc=bar.timestamp_utc,
                payload=exit_decision.to_payload(),
            )
            logger.info("EXIT %s %s", bar.timestamp_utc, exit_decision.reason)
        logger.info(
            "bar %s close=%.2f session_pnl=%.2f dll=%s",
            bar.timestamp_utc, bar.close, self._session_pnl, self._daily_limit_hit,
        )
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_live_recording.py -q` — expected: 3 passed.

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q`

```bash
git add src/full_python/live/recording.py tests/test_live_recording.py
git commit -m "feat: observe-mode signal recording strategy wrapper"
```

---

### Task 5: Session shadow report

**Files:**
- Create: `src/full_python/live/session_report.py`
- Test: `tests/test_live_session_report.py` (new)

**Interfaces:**
- Consumes: `EventLedger.read_jsonl`, `EventType`, `MarketBar`;
  `AdaptiveTrendStrategy(production_am_config())`;
  `SimulationConfig`/`SimulationEngine` from `full_python.simulation`;
  `FROZEN_SIMULATION_OVERRIDES` from `scripts.freeze_baseline_anchor`.
- Produces (used by Task 7):
  `bars_from_ledger(ledger) -> list[MarketBar]`,
  `recorded_signals(ledger) -> list[dict]`,
  `replay_signals(bars) -> list[dict]`,
  `diff_signals(live, replay) -> list[str]`,
  `run_report(events_path, html_path) -> int` (0 = PARITY, 1 = divergence).
  Signal dicts: entries
  `{"minute", "kind": "entry", "side", "quantity", "stop_price"}`, exits
  `{"minute", "kind": "exit", "reason"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_session_report.py`:

```python
from __future__ import annotations

import json

from full_python.events import EventLedger, EventType
from full_python.live.session_report import (
    bars_from_ledger,
    diff_signals,
    recorded_signals,
    replay_signals,
    run_report,
)
from full_python.models import MarketBar


def _bar_payload(close: float) -> dict:
    return {"symbol": "NQU6", "open": close, "high": close + 0.5,
            "low": close - 0.5, "close": close, "volume": 10.0}


def _ledger_with_bars(count: int = 5) -> EventLedger:
    ledger = EventLedger()
    for index in range(count):
        ledger.append(
            EventType.BAR,
            timestamp_utc=f"2026-07-10T18:{31 + index:02d}:00Z",  # 14:31+ ET: quiet hours
            payload=_bar_payload(100.0 + index),
        )
    return ledger


def test_bars_roundtrip_from_ledger() -> None:
    ledger = _ledger_with_bars(3)
    bars = bars_from_ledger(ledger)
    assert [type(b) for b in bars] == [MarketBar] * 3
    assert bars[0].timestamp_utc == "2026-07-10T18:31:00Z"
    assert bars[2].close == 102.0
    assert bars[0].symbol == "NQU6"


def test_quiet_session_is_parity(tmp_path) -> None:
    ledger = _ledger_with_bars(5)
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)
    html = tmp_path / "report.html"

    exit_code = run_report(events, html)

    assert exit_code == 0
    text = html.read_text(encoding="utf-8")
    assert "PARITY" in text
    assert "DIVERGENCE" not in text


def test_bogus_recorded_signal_is_divergence(tmp_path) -> None:
    ledger = _ledger_with_bars(5)
    ledger.append(
        EventType.ORDER_INTENT,
        timestamp_utc="2026-07-10T18:33:00Z",
        payload={"symbol": "NQU6", "side": "buy", "quantity": 1,
                 "reason": "adaptive_trend", "stop_price": 95.0},
    )
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)
    html = tmp_path / "report.html"

    exit_code = run_report(events, html)

    assert exit_code == 1
    text = html.read_text(encoding="utf-8")
    assert "DIVERGENCE" in text
    assert "18:33" in text  # the divergent minute is named


def test_diff_reports_index_and_both_sides() -> None:
    live = [{"minute": "m1", "kind": "entry", "side": "buy",
             "quantity": 1, "stop_price": 95.0}]
    divergences = diff_signals(live, [])
    assert len(divergences) == 1
    assert "live=" in divergences[0] and "replay=" in divergences[0]
    assert diff_signals([], []) == []


def test_halts_are_listed_in_the_report(tmp_path) -> None:
    ledger = _ledger_with_bars(2)
    ledger.append(
        EventType.STATE_TRANSITION,
        timestamp_utc="2026-07-10T18:32:30Z",
        payload={"transition": "execution_halt", "reason": "data_outage",
                 "error": "no bar within grace"},
    )
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)
    html = tmp_path / "report.html"

    run_report(events, html)

    text = html.read_text(encoding="utf-8")
    assert "data_outage" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_live_session_report.py -q`
Expected: FAIL — no module `full_python.live.session_report`.

- [ ] **Step 3: Implement `src/full_python/live/session_report.py`**

```python
"""Post-session shadow report for observe-mode sessions.

Replays the recorded bars through the identical no-fill strategy stack
and diffs the signal streams. In observe mode the live strategy never
receives fills (orders are disabled), so the like-for-like comparison
is a fill-free replay: same bars, same production config,
on_bar_context(0.0, False) every bar -- exactly what the live wrapper
saw. Full fill-level parity belongs to the later order-test/paper
gates.

Also renders an informational "what the sim would have traded" section
using the frozen baseline simulation config; it plays no part in the
PARITY verdict.
"""
from __future__ import annotations

import html as html_module
import logging
from pathlib import Path
from typing import Any, Optional

from full_python.events import EventLedger, EventType
from full_python.models import MarketBar
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import production_am_config

logger = logging.getLogger("full_python.live")


def bars_from_ledger(ledger: EventLedger) -> list:
    bars = []
    for record in ledger.records:
        if record.event_type is not EventType.BAR:
            continue
        payload = record.payload
        bars.append(MarketBar(
            timestamp_utc=record.timestamp_utc,
            symbol=str(payload["symbol"]),
            open=float(payload["open"]),
            high=float(payload["high"]),
            low=float(payload["low"]),
            close=float(payload["close"]),
            volume=float(payload["volume"]),
        ))
    return bars


def recorded_signals(ledger: EventLedger) -> list:
    signals = []
    for record in ledger.records:
        if record.event_type is EventType.ORDER_INTENT:
            signals.append({
                "minute": record.timestamp_utc, "kind": "entry",
                "side": record.payload.get("side"),
                "quantity": record.payload.get("quantity"),
                "stop_price": record.payload.get("stop_price"),
            })
        elif record.event_type is EventType.EXIT:
            signals.append({
                "minute": record.timestamp_utc, "kind": "exit",
                "reason": record.payload.get("reason"),
            })
    return signals


def replay_signals(bars: list) -> list:
    strategy = AdaptiveTrendStrategy(production_am_config())
    signals = []
    for bar in bars:
        strategy.on_bar_context(session_pnl=0.0, daily_limit_hit=False)
        result = strategy.on_bar(bar)
        for intent in result.order_intents:
            signals.append({
                "minute": bar.timestamp_utc, "kind": "entry",
                "side": intent.side, "quantity": intent.quantity,
                "stop_price": intent.metadata.get("stop_price"),
            })
        for exit_decision in result.exits:
            signals.append({
                "minute": bar.timestamp_utc, "kind": "exit",
                "reason": exit_decision.reason,
            })
    return signals


def diff_signals(live: list, replay: list) -> list:
    divergences = []
    for index in range(max(len(live), len(replay))):
        lhs = live[index] if index < len(live) else None
        rhs = replay[index] if index < len(replay) else None
        if lhs != rhs:
            minute = (lhs or rhs or {}).get("minute", "?")
            divergences.append(
                f"signal #{index + 1} at {minute}: live={lhs!r} replay={rhs!r}"
            )
    return divergences


def _halts(ledger: EventLedger) -> list:
    return [
        record for record in ledger.records
        if record.event_type is EventType.STATE_TRANSITION
    ]


def _sim_trades(bars: list) -> list:
    from scripts.freeze_baseline_anchor import FROZEN_SIMULATION_OVERRIDES
    from full_python.simulation import SimulationConfig, SimulationEngine

    config = SimulationConfig(**FROZEN_SIMULATION_OVERRIDES)
    result = SimulationEngine(config).run(
        bars, AdaptiveTrendStrategy(production_am_config())
    )
    return list(result.trades)


def _esc(value: Any) -> str:
    return html_module.escape(str(value))


def _write_html(path: Path, *, bars, live, replay, divergences, halts, sim_trades) -> None:
    verdict = "PARITY" if not divergences else "DIVERGENCE"
    color = "#0a7d33" if not divergences else "#b00020"
    rows = "".join(
        f"<tr><td>{_esc(s['minute'])}</td><td>{_esc(s['kind'])}</td>"
        f"<td>{_esc(s.get('side', s.get('reason', '')))}</td>"
        f"<td>{_esc(s.get('quantity', ''))}</td>"
        f"<td>{_esc(s.get('stop_price', ''))}</td></tr>"
        for s in live
    ) or "<tr><td colspan='5'>no signals recorded</td></tr>"
    diff_rows = "".join(f"<li>{_esc(line)}</li>" for line in divergences)
    halt_rows = "".join(
        f"<li>{_esc(r.timestamp_utc)} — {_esc(r.payload.get('reason'))}: "
        f"{_esc(r.payload.get('error', ''))}</li>"
        for r in halts
    ) or "<li>none</li>"
    trade_rows = "".join(
        f"<tr><td>{_esc(t.entry_timestamp_utc)}</td><td>{_esc(t.side)}</td>"
        f"<td>{_esc(t.quantity)}</td><td>{_esc(t.exit_reason)}</td>"
        f"<td>{t.net_pnl:+.2f}</td></tr>"
        for t in sim_trades
    ) or "<tr><td colspan='5'>none</td></tr>"
    net = sum(t.net_pnl for t in sim_trades)
    body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Observe session report</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; }} td, th {{ border: 1px solid #ccc; padding: 4px 10px; }}
.verdict {{ color: white; background: {color}; display: inline-block;
            padding: 6px 16px; font-weight: 700; border-radius: 4px; }}
</style></head><body>
<h1>Observe session shadow report</h1>
<p><span class="verdict">{verdict}</span></p>
<p>{len(bars)} bars, {len(live)} live signals, {len(replay)} replay signals.</p>
<h2>Divergences</h2><ul>{diff_rows or "<li>none</li>"}</ul>
<h2>Halts</h2><ul>{halt_rows}</ul>
<h2>Live signals</h2>
<table><tr><th>minute (UTC)</th><th>kind</th><th>side/reason</th><th>qty</th><th>stop</th></tr>{rows}</table>
<h2>Informational: sim trades on these bars (frozen baseline config)</h2>
<p>Net: {net:+.2f}. Not part of the verdict.</p>
<table><tr><th>entry (UTC)</th><th>side</th><th>qty</th><th>exit</th><th>net P&amp;L</th></tr>{trade_rows}</table>
</body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def run_report(events_path, html_path) -> int:
    ledger = EventLedger.read_jsonl(events_path)
    bars = bars_from_ledger(ledger)
    live = recorded_signals(ledger)
    replay = replay_signals(bars)
    divergences = diff_signals(live, replay)
    halts = _halts(ledger)
    sim_trades = _sim_trades(bars)
    _write_html(Path(html_path), bars=bars, live=live, replay=replay,
                divergences=divergences, halts=halts, sim_trades=sim_trades)
    for line in divergences:
        logger.error("DIVERGENCE %s", line)
    for record in halts:
        logger.warning("HALT %s %s", record.timestamp_utc, record.payload)
    logger.info("verdict: %s (%d bars, %d live signals) -> %s",
                "PARITY" if not divergences else "DIVERGENCE",
                len(bars), len(live), html_path)
    return 1 if divergences else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_live_session_report.py -q`
Expected: 5 passed. (The replay uses the REAL production strategy on a
handful of quiet mid-afternoon bars — outside the entry window it emits
nothing, so the quiet session is PARITY and the injected bogus intent is
a divergence. If `SimulationConfig(**FROZEN_SIMULATION_OVERRIDES)`
raises on an unexpected key, read `scripts/freeze_baseline_anchor.py`
and construct the config the same way the identity test
`tests/test_live_loop_identity.py` does — do not change the overrides.)

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q`

```bash
git add src/full_python/live/session_report.py tests/test_live_session_report.py
git commit -m "feat: post-session shadow parity report for observe sessions"
```

---

### Task 6: Risk probe

**Files:**
- Create: `src/full_python/live/risk_probe.py`
- Test: `tests/test_live_risk_probe.py` (new)

**Interfaces:**
- Consumes: `TradovateHttpClient.get`, `TradovateError`, `_redact` from
  `full_python.tradovate.http`.
- Produces (used by Task 7): `PROBE_ENDPOINTS: tuple`,
  `run_risk_probe(http, out_path) -> dict` — GET-only snapshot written as
  pretty JSON to `out_path`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_risk_probe.py`:

```python
from __future__ import annotations

import json

from full_python.live.risk_probe import PROBE_ENDPOINTS, run_risk_probe
from full_python.tradovate.errors import TradovateRequestError


class GetOnlyHttp:
    """Fake http client with NO post attribute: any POST would AttributeError."""

    def __init__(self, failures=()):
        self.gets = []
        self._failures = set(failures)

    def get(self, path):
        self.gets.append(path)
        if path in self._failures:
            raise TradovateRequestError("Tradovate request failed with status 404")
        return [{"id": 1, "name": "DEMO123", "accessToken": "sekret"}]


def test_probe_gets_every_endpoint_and_writes_snapshot(tmp_path) -> None:
    http = GetOnlyHttp()
    out = tmp_path / "session" / "account_risk.json"

    snapshot = run_risk_probe(http, out)

    assert http.gets == list(PROBE_ENDPOINTS)
    assert "/userAccountAutoLiq/list" in PROBE_ENDPOINTS  # the DLL evidence
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert set(on_disk) == set(PROBE_ENDPOINTS)
    assert on_disk == snapshot


def test_probe_records_endpoint_failures_without_dying(tmp_path) -> None:
    http = GetOnlyHttp(failures={"/marginSnapshot/list"})
    out = tmp_path / "account_risk.json"

    snapshot = run_risk_probe(http, out)

    assert "error" in snapshot["/marginSnapshot/list"]
    assert isinstance(snapshot["/account/list"], list)


def test_probe_redacts_sensitive_keys(tmp_path) -> None:
    out = tmp_path / "account_risk.json"
    snapshot = run_risk_probe(GetOnlyHttp(), out)
    assert snapshot["/account/list"][0]["accessToken"] == "<redacted>"
    assert "sekret" not in out.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_live_risk_probe.py -q`
Expected: FAIL — no module `full_python.live.risk_probe`.

- [ ] **Step 3: Implement `src/full_python/live/risk_probe.py`**

```python
"""Read-only account risk snapshot (GET only -- never a POST).

Captures the demo account's platform-side risk configuration at session
start. /userAccountAutoLiq/list is the direct evidence for the open
operational question: does Tradovate/the prop firm enforce an
account-level daily-loss limit, and does it force-flatten or only block
new orders (parent adapter spec, Open Operational Decisions). This
module records; interpretation happens in the order-test spec.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from full_python.tradovate.errors import TradovateError
from full_python.tradovate.http import _redact

PROBE_ENDPOINTS = (
    "/account/list",
    "/cashBalance/list",
    "/userAccountAutoLiq/list",
    "/marginSnapshot/list",
)


def run_risk_probe(http, out_path) -> dict:
    snapshot = {}
    for endpoint in PROBE_ENDPOINTS:
        try:
            snapshot[endpoint] = _redact(http.get(endpoint))
        except TradovateError as exc:
            snapshot[endpoint] = {"error": str(exc)}
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    return snapshot
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_live_risk_probe.py -q` — expected: 3 passed.

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q`

```bash
git add src/full_python/live/risk_probe.py tests/test_live_risk_probe.py
git commit -m "feat: read-only account risk probe for the DLL open question"
```

---

### Task 7: Runner composition + CLI

**Files:**
- Create: `src/full_python/live/runner.py`
- Create: `src/full_python/live/__main__.py`
- Test: `tests/test_live_runner.py` (new)

**Interfaces:**
- Consumes: everything above; `LiveLoop`, `RiskSupervisor(RiskSupervisorConfig(point_value=20.0))`,
  `LiveBarSource(feed, clock, authority, active_window, position_provider)`,
  `ActiveWindow(start_minutes_et, end_minutes_et)`, `ContractAuthority(root)`,
  `TradovateMarketDataFeed(ws, symbol=...)` + `.subscribe(closest_timestamp=..., bars_back=...)`,
  `TradovateBroker(config, rest_client)`, `classify_timestamp(iso) -> SessionInfo`
  (`.session_date: date`, `.minutes_from_midnight_et: int`),
  `production_am_config()` (`.entry_start_minutes_et`, `.entry_end_minutes_et`),
  `credentials_from_env`, `TradovateAuthClient`, `TradovateHttpClient`,
  `UrllibHttpTransport`, `TradovateWebSocketClient`, `connect_websocket`.
- Produces: `observe_adapter_config(account_spec, account_id, root_symbol="NQ") -> TradovateAdapterConfig`,
  `bars_until(source, clock, end_minutes_et, maintenance=None) -> Iterator[MarketBar]`,
  `build_observe_session(...) -> ObserveSession` (dataclass with fields
  `loop, broker, ledger, events_path, report_path, feed, ws`),
  `run_observe_session(session) -> int`, `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_runner.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from full_python.live.runner import (
    bars_until,
    build_observe_session,
    main,
    observe_adapter_config,
    run_observe_session,
)
from full_python.models import MarketBar
from full_python.tradovate.errors import TradovateOrderSafetyError


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current = self.current + timedelta(**kwargs)


class FakeChartWs:
    """Implements the ChartWebSocketClient protocol with scripted events."""

    def __init__(self, events) -> None:
        self.events = list(events)
        self.requests = []
        self.closed = False

    def request(self, endpoint, payload):
        self.requests.append((endpoint, payload))
        if endpoint == "md/getChart":
            return {"historicalId": 1, "realtimeId": 2}
        return {}

    def receive_event(self, timeout_seconds):
        if self.events:
            return self.events.pop(0)
        return None

    def close(self):
        self.closed = True


def _chart_event(ts: str, price: float, symbol_unused: str = "") -> dict:
    return {"e": "chart", "d": {"charts": [{"id": 2, "bars": [{
        "timestamp": ts, "open": price, "high": price, "low": price,
        "close": price, "volume": 1,
    }]}]}}


def _bar(ts: str, price: float = 100.0) -> MarketBar:
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=price, high=price,
                     low=price, close=price, volume=1.0)


def test_observe_adapter_config_pins_orders_off() -> None:
    config = observe_adapter_config("DEMO123", 456)
    assert config.order_enabled is False
    assert config.flatten_enabled is False
    assert config.environment.name == "demo"
    assert config.dollar_point_value == 20.0


def test_cli_has_no_flag_that_could_enable_orders() -> None:
    with pytest.raises(SystemExit):  # argparse rejects unknown flags
        main(["--order-enabled"])
    with pytest.raises(SystemExit):
        main(["--flatten-enabled"])
    with pytest.raises(SystemExit):
        main(["--environment", "live"])


def test_bars_until_stops_at_end_time_and_runs_maintenance() -> None:
    # 18:31 UTC == 14:31 ET (July); end at 14:33 ET = 873 minutes
    clock = FakeClock(datetime(2026, 7, 10, 18, 31, 30, tzinfo=timezone.utc))
    bars = [_bar("2026-07-10T18:31:00Z"), _bar("2026-07-10T18:32:00Z"),
            _bar("2026-07-10T18:33:00Z")]
    calls = []

    def maintenance():
        calls.append(clock.now())
        clock.advance(minutes=1)

    taken = list(bars_until(iter(bars), clock, 14 * 60 + 33, maintenance))

    assert len(taken) == 2  # third bar never pulled: end time hit after bar 2
    assert len(calls) == 2


def test_build_and_run_observe_session_end_to_end(tmp_path) -> None:
    """Full offline session: scripted chart events -> LiveLoop -> JSONL ->
    PARITY report. The broker's REST client is the sentinel: any order
    call would raise."""
    clock = FakeClock(datetime(2026, 7, 10, 18, 31, 30, tzinfo=timezone.utc))
    # Front contract for 2026-07-10 is NQU6 (Sep 2026) per the roll rule.
    ws = FakeChartWs([
        _chart_event("2026-07-10T18:31:00.000Z", 100.0),
        _chart_event("2026-07-10T18:32:00.000Z", 101.0),
        _chart_event("2026-07-10T18:33:00.000Z", 102.0),
    ])

    session = build_observe_session(
        ws_client=ws, clock=clock, account_spec="DEMO123", account_id=456,
        data_dir=tmp_path, bars_back=10, end_minutes_et=14 * 60 + 33,
        maintenance=lambda: clock.advance(minutes=1),
    )

    assert session.events_path.parent.name == "2026-07-10"
    subscribe = [r for r in ws.requests if r[0] == "md/getChart"]
    assert subscribe and subscribe[0][1]["symbol"] == "NQU6"

    exit_code = run_observe_session(session)

    assert exit_code == 0  # clean session, PARITY
    assert ws.closed
    cancel = [r for r in ws.requests if r[0] == "md/cancelChart"]
    assert cancel == [("md/cancelChart", {"subscriptionId": 2})]
    text = session.events_path.read_text(encoding="utf-8")
    assert text.count('"bar"') == 2  # two bars before end time
    assert session.report_path.exists()
    assert "PARITY" in session.report_path.read_text(encoding="utf-8")


def test_observe_broker_rest_sentinel_raises_on_any_call(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 7, 10, 18, 31, 30, tzinfo=timezone.utc))
    session = build_observe_session(
        ws_client=FakeChartWs([]), clock=clock, account_spec="D", account_id=1,
        data_dir=tmp_path, bars_back=10, end_minutes_et=14 * 60 + 33,
    )
    rest = session.broker._rest_client
    with pytest.raises(TradovateOrderSafetyError, match="observe mode"):
        rest.order_place({"orderQty": 1})


def test_report_only_mode_runs_offline(tmp_path) -> None:
    from full_python.events import EventLedger, EventType

    ledger = EventLedger()
    ledger.append(EventType.BAR, timestamp_utc="2026-07-10T18:31:00Z",
                  payload={"symbol": "NQU6", "open": 1.0, "high": 1.0,
                           "low": 1.0, "close": 1.0, "volume": 1.0})
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)

    assert main(["--report-only", str(events)]) == 0
    assert (tmp_path / "report.html").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_live_runner.py -q`
Expected: FAIL — no module `full_python.live.runner`.

- [ ] **Step 3: Implement `src/full_python/live/runner.py`**

```python
"""Observe-mode live session runner (Gate 5, demo environment).

Composition root only: wires existing pieces together. Observe mode is
pinned HERE as literals (observe_adapter_config) -- no CLI flag, env
var, or parameter exists to enable orders, and the broker's REST client
is a sentinel that raises on any attribute access, so even a future
code path that tried to place an order would fail loudly. Enabling
orders is a different spec (the demo order test), not a config change.

Shutdown model: the persistent ledger flushes every event, so Ctrl+C
and crashes lose nothing; the runner's job on exit is only to cancel
the chart subscription, close the socket, and run the shadow report.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from full_python.data.sessions import classify_timestamp
from full_python.execution.live_loop import LiveLoop
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.live.persistence import PersistentEventLedger
from full_python.live.recording import RecordingStrategy
from full_python.live.risk_probe import run_risk_probe
from full_python.live.session_report import run_report
from full_python.livedata.clock import Clock, SystemClock
from full_python.livedata.contract_authority import ContractAuthority
from full_python.livedata.live_bar_source import ActiveWindow, LiveBarSource
from full_python.models import MarketBar
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import production_am_config
from full_python.tradovate.auth import TradovateAuthClient
from full_python.tradovate.broker import TradovateBroker
from full_python.tradovate.config import (
    DEMO_ENVIRONMENT,
    TradovateAdapterConfig,
    credentials_from_env,
)
from full_python.tradovate.errors import TradovateOrderSafetyError
from full_python.tradovate.feed import TradovateMarketDataFeed
from full_python.tradovate.http import TradovateHttpClient, UrllibHttpTransport
from full_python.tradovate.transport import connect_websocket
from full_python.tradovate.ws import TradovateWebSocketClient

logger = logging.getLogger("full_python.live")

NQ_DOLLAR_POINT_VALUE = 20.0


def observe_adapter_config(
    account_spec: str, account_id: int, root_symbol: str = "NQ"
) -> TradovateAdapterConfig:
    # The ONLY adapter config this runner can produce. Observe literals,
    # pinned by tests/test_live_runner.py; changing them is a spec change.
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT,
        account_spec=account_spec,
        account_id=account_id,
        root_symbol=root_symbol,
        order_enabled=False,
        flatten_enabled=False,
        dollar_point_value=NQ_DOLLAR_POINT_VALUE,
    )


class _NoOrderRestClient:
    """TradovateBroker with orders disabled never touches its REST
    client; this sentinel turns any attempt into a loud failure."""

    def __getattr__(self, name: str):
        raise TradovateOrderSafetyError(
            f"observe mode attempted broker REST call {name!r}"
        )


def now_utc_iso(clock: Clock) -> str:
    return clock.now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bars_until(
    source: Iterable[MarketBar],
    clock: Clock,
    end_minutes_et: int,
    maintenance: Optional[Callable[[], None]] = None,
) -> Iterator[MarketBar]:
    """Yields bars until the ET wall clock passes end_minutes_et; runs
    maintenance (token renewal) between bars. Returning ends LiveLoop's
    iteration cleanly (close_end_of_data path)."""
    for bar in source:
        yield bar
        if maintenance is not None:
            maintenance()
        session = classify_timestamp(now_utc_iso(clock))
        if session.minutes_from_midnight_et >= end_minutes_et:
            logger.info("session end time reached; stopping")
            return


@dataclass
class ObserveSession:
    loop: LiveLoop
    broker: TradovateBroker
    ledger: PersistentEventLedger
    events_path: Path
    report_path: Path
    feed: TradovateMarketDataFeed
    ws: object


def build_observe_session(
    *,
    ws_client,
    clock: Clock,
    account_spec: str,
    account_id: int,
    data_dir: Path,
    bars_back: int,
    end_minutes_et: int,
    symbol_root: str = "NQ",
    maintenance: Optional[Callable[[], None]] = None,
) -> ObserveSession:
    session_info = classify_timestamp(now_utc_iso(clock))
    session_dir = Path(data_dir) / session_info.session_date.isoformat()
    events_path = session_dir / "events.jsonl"
    report_path = session_dir / "report.html"

    authority = ContractAuthority(symbol_root)
    front = authority.front_contract(session_info.session_date)
    logger.info("front contract for %s: %s", session_info.session_date, front)
    feed = TradovateMarketDataFeed(ws_client, symbol=front)
    feed.subscribe(closest_timestamp=now_utc_iso(clock), bars_back=bars_back)

    ledger = PersistentEventLedger(events_path)
    strategy_config = production_am_config()
    strategy = RecordingStrategy(AdaptiveTrendStrategy(strategy_config), ledger)
    broker = TradovateBroker(
        observe_adapter_config(account_spec, account_id, symbol_root),
        _NoOrderRestClient(),
    )
    window = ActiveWindow(
        strategy_config.entry_start_minutes_et, strategy_config.entry_end_minutes_et
    )
    source = LiveBarSource(
        feed, clock, authority, window,
        position_provider=lambda: broker.position is not None,
    )
    bar_stream = bars_until(source, clock, end_minutes_et, maintenance)
    supervisor = RiskSupervisor(RiskSupervisorConfig(point_value=NQ_DOLLAR_POINT_VALUE))
    loop = LiveLoop(bar_stream, strategy, broker, supervisor, ledger)
    return ObserveSession(
        loop=loop, broker=broker, ledger=ledger, events_path=events_path,
        report_path=report_path, feed=feed, ws=ws_client,
    )


def run_observe_session(session: ObserveSession) -> int:
    halted: Optional[str] = None
    try:
        result = session.loop.run()
        halted = result.halted_reason
    except KeyboardInterrupt:
        logger.info("operator interrupt (Ctrl+C); ending session")
    finally:
        for closer in (session.feed.cancel, session.ws.close, session.ledger.close):
            try:
                closer()
            except Exception as exc:  # best-effort shutdown; report still runs
                logger.warning("shutdown step failed: %s", exc)
    if halted is not None:
        logger.error("HALT: %s", halted)
    logger.info("events: %s", session.events_path)
    report_exit = run_report(session.events_path, session.report_path)
    return report_exit if halted is None else 2


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m full_python.live",
        description=(
            "Gate 5 observe-mode demo session runner. Orders are impossible "
            "by construction; there is no flag to enable them."
        ),
    )
    parser.add_argument("--data-dir", default="runs/live")
    parser.add_argument("--end-et", default="16:05",
                        help="ET wall-clock session end (HH:MM), default 16:05")
    parser.add_argument("--bars-back", type=int, default=400,
                        help="history bars for indicator warm-up (default 400)")
    parser.add_argument("--symbol-root", default="NQ")
    parser.add_argument("--report-only", metavar="EVENTS_JSONL", default=None,
                        help="skip the session; rebuild the report from a JSONL")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.report_only is not None:
        events_path = Path(args.report_only)
        return run_report(events_path, events_path.with_name("report.html"))

    hours, minutes = args.end_et.split(":")
    end_minutes_et = int(hours) * 60 + int(minutes)

    credentials = credentials_from_env()
    http = TradovateHttpClient(DEMO_ENVIRONMENT.rest_base_url, UrllibHttpTransport())
    auth = TradovateAuthClient(http, credentials)
    token = auth.request_access_token()
    authed_http = http.with_access_token(token.access_token)

    accounts = authed_http.account_list()
    if not isinstance(accounts, list) or not accounts:
        raise SystemExit("no Tradovate accounts visible with these credentials")
    account = accounts[0]
    logger.info("account: %s (id %s)", account.get("name"), account.get("id"))

    clock = SystemClock()
    session_dir = (
        Path(args.data_dir)
        / classify_timestamp(now_utc_iso(clock)).session_date.isoformat()
    )
    run_risk_probe(authed_http, session_dir / "account_risk.json")

    transport = connect_websocket(DEMO_ENVIRONMENT.md_ws_base_url)
    ws_client = TradovateWebSocketClient(transport)
    ws_client.authorize(token.md_access_token)

    token_state = {"token": token}

    def maintenance() -> None:
        if token_state["token"].should_renew(clock.now()):
            token_state["token"] = auth.renew_access_token(token_state["token"])
            logger.info("REST access token renewed")

    session = build_observe_session(
        ws_client=ws_client, clock=clock,
        account_spec=str(account.get("name")), account_id=int(account["id"]),
        data_dir=Path(args.data_dir), bars_back=args.bars_back,
        end_minutes_et=end_minutes_et, symbol_root=args.symbol_root,
        maintenance=maintenance,
    )
    return run_observe_session(session)
```

Create `src/full_python/live/__main__.py`:

```python
from __future__ import annotations

from full_python.live.runner import main

raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_live_runner.py -q`
Expected: 6 passed. Debug notes if not:
- If the end-to-end test hangs, the FakeClock never advanced past the
  end time — check that `maintenance` (which advances the clock) is
  passed through `build_observe_session` into `bars_until`.
- If `LiveBarSource` raises `DataIntegrityError` on the symbol, verify
  the front contract for 2026-07-10 really is `NQU6`
  (`python3 -c "from datetime import date; from full_python.data.databento import front_contract_for_session as f; print(f(date(2026,7,10),'NQ'))"`)
  and fix the TEST's chart symbol expectation — never the authority.
- If the report step fails on `MarketBar` volume, check the feed's
  volume key (`upVolume` in `chart_bar_to_vendor_bar`) matches the
  existing feed tests at the top of `tests/test_tradovate_feed.py`.

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q`

```bash
git add src/full_python/live/runner.py src/full_python/live/__main__.py tests/test_live_runner.py
git commit -m "feat: observe-mode live session runner with pinned demo/no-order config"
```

---

### Task 8: Documentation closure + runbook

**Files:**
- Create: `docs/live-observe-runbook.md`
- Modify: `docs/superpowers/specs/2026-07-11-live-observe-gate-design.md` (probe endpoint line)
- Modify: `HANDOFF.md` (open tasks + repo orientation)

**Interfaces:** none (docs only).

- [ ] **Step 1: Write the runbook**

Create `docs/live-observe-runbook.md`:

```markdown
# Gate 5 — Observe Session Runbook

Attended demo-observe session on the operator's Mac. Orders are
impossible by construction (observe literals in
`full_python/live/runner.py`; sentinel REST client).

## One-time setup

Export the seven credential variables in the shell that runs the
session (never commit them, never echo them):

    export TRADOVATE_USERNAME=...
    export TRADOVATE_PASSWORD=...
    export TRADOVATE_APP_ID=...
    export TRADOVATE_APP_VERSION=...
    export TRADOVATE_CLIENT_ID=...
    export TRADOVATE_SECRET=...
    export TRADOVATE_DEVICE_ID=...

## Per session

1. Start any time after ~9:00 ET (before the 9:30 window):

       python3 -m full_python.live

   Options: `--data-dir runs/live` (default), `--end-et 16:05`,
   `--bars-back 400`, `--symbol-root NQ`.
2. Watch the console. Every bar logs one line; signals log as
   `SIGNAL`/`EXIT`; halts are loud `HALT:` lines with the reason.
3. End: Ctrl+C anytime, or the runner stops itself at `--end-et`.
   Either way it writes artifacts and prints the parity verdict.
4. Rebuild a report later: `python3 -m full_python.live --report-only
   runs/live/<date>/events.jsonl`.

## Artifacts (per session, under `runs/live/<session-date>/`)

- `events.jsonl` — full event ledger (crash-safe, append-per-event)
- `account_risk.json` — GET-only risk probe (autoLiq = the DLL evidence)
- `report.html` — shadow parity report (verdict, signals, halts, sim info)

## Gate 5 pass criteria (pre-registered in the spec)

3 clean sessions, each with: exact PARITY verdict; every
disconnect/outage handled by the documented halt policy; probe output
captured. Divergent or unexplained sessions do not count and open a
bar-level debug from the ledger.

| # | Date | Verdict | Halts (reason) | Probe captured | Clean? |
|---|------|---------|----------------|----------------|--------|
| 1 |      |         |                |                |        |
| 2 |      |         |                |                |        |
| 3 |      |         |                |                |        |
```

- [ ] **Step 2: Amend the spec's probe endpoint line**

In `docs/superpowers/specs/2026-07-11-live-observe-gate-design.md`,
replace `` `cashBalance/getcashbalancesnapshot` `` with
`` `cashBalance/list` `` and append to that sentence:
`(amended at plan time: the snapshot endpoint is a POST; the GET-only
rule outranks the endpoint list)`.

- [ ] **Step 3: Update HANDOFF.md**

In §6 Open tasks, replace item 2's first sentence tail
"First point real credentials and broker decisions are needed." with:
"Slice 1 (Gate 5 observe runner) is BUILT — see
`docs/live-observe-runbook.md`; next action is running the 3 observe
sessions, then the demo-order-test spec."
In §7 repo orientation, extend the `src/full_python/` list with:
"`live/` (observe-mode session runner, shadow report, risk probe),
`tradovate/transport.py` (real RFC 6455 client)".

- [ ] **Step 4: Full suite, then commit**

Run: `python3 -m pytest -q` — everything green (expected ≈294 + ~32 new
passed, 3 skipped; trust green/red, not the exact count).

```bash
git add docs/live-observe-runbook.md docs/superpowers/specs/2026-07-11-live-observe-gate-design.md HANDOFF.md
git commit -m "docs: Gate 5 observe runbook and handoff update"
```

---

## Plan Self-Review

- **Spec coverage:** transport (spec component 1) → T1+T2; runner/CLI +
  lifecycle + shutdown (component 2, error handling) → T7; JSONL sink
  (component 3) → T3; shadow report (component 4) → T5 (recording half
  → T4); risk probe (component 5) → T6; observe-pin +
  no-order-flag tests (spec Decision 1) → T7 tests; acceptance
  criteria + runbook + spec amendment → T8. Real-socket path
  deliberately untested per spec Testing section.
- **Type consistency:** `WebSocketConnection(sock, prebuffer, monotonic_clock)`
  defined T2, used by `connect_websocket` T2 and imported T7;
  `PersistentEventLedger(path)` T3 → T7; `RecordingStrategy(inner, ledger)`
  T4 → T7; signal-dict shape identical in T5's `recorded_signals`/
  `replay_signals` and T4's payload keys; `run_report(events, html) -> int`
  T5 → T7; `ObserveSession` fields listed in T7's Interfaces block.
- **Known execution notes:** (a) T7's end-to-end test depends on the
  roll rule making NQU6 the 2026-07-10 front contract — verified at plan
  time (`front_contract_for_session(date(2026,7,10),'NQ') == "NQU6"`) and
  a re-check command is included in T7 Step 4; (b) the fake chart events
  use the explicit `volume` key because `_volume` otherwise requires
  BOTH `upVolume` and `downVolume` (checked against feed.py at plan
  time); (c) expected suite counts after T1+ are approximate — trust
  green/red.
```
