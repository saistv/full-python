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


def read_frame(
    read_exact: Callable[[int], bytes], *, allow_masked: bool = True
) -> Tuple[int, bool, bytes]:
    first, second = read_exact(2)
    if first & 0x70:
        raise TradovateWebSocketError("Unsupported RSV bits in websocket frame")
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    if masked and not allow_masked:
        # RFC 6455 5.1: a client MUST mask, a server MUST NOT. Frames
        # arriving from the server with the mask bit set are protocol
        # violations, not legitimate data.
        raise TradovateWebSocketError("masked websocket frame received from server")
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
                    lambda count: self._read_exact(count, progress),
                    allow_masked=False,
                )
            except socket.timeout:
                if progress.consumed or fragments:
                    raise TradovateWebSocketError("timeout mid-frame on websocket")
                return None
            if opcode in (OPCODE_CLOSE, OPCODE_PING, OPCODE_PONG) and (
                not fin or len(payload) > 125
            ):
                # RFC 6455 5.5: control frames must not be fragmented and
                # must carry payloads no longer than 125 bytes.
                raise TradovateWebSocketError("invalid control frame on websocket")
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
