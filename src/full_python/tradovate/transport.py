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
