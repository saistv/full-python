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


def test_receive_rejects_masked_frame_from_server() -> None:
    masked = bytes([0x81, 0x85, 0x00, 0x00, 0x00, 0x00]) + b"Hello"
    conn = WebSocketConnection(FakeSocket([masked]))
    with pytest.raises(TradovateWebSocketError, match="masked"):
        conn.receive(5.0)


def test_receive_rejects_fragmented_control_frame() -> None:
    # Ping with FIN=0 violates RFC 6455 5.5: control frames must not be fragmented.
    bad_ping = bytes([0x09, 0x02]) + b"hi"
    conn = WebSocketConnection(FakeSocket([bad_ping]))
    with pytest.raises(TradovateWebSocketError, match="control"):
        conn.receive(5.0)


def test_receive_rejects_oversized_control_frame_payload() -> None:
    # Ping with a 126-byte payload violates RFC 6455 5.5: control frames
    # must carry payloads no longer than 125 bytes.
    bad_ping = bytes([0x89, 0x7E]) + (126).to_bytes(2, "big") + b"a" * 126
    conn = WebSocketConnection(FakeSocket([bad_ping]))
    with pytest.raises(TradovateWebSocketError, match="control"):
        conn.receive(5.0)


def test_mid_frame_timeout_closes_the_connection() -> None:
    # One header byte arrives, then a timeout occurs mid-frame.
    # The connection must be marked closed so a subsequent receive() raises
    # the "closed" error, not attempting to reparse leftover bytes.
    conn = WebSocketConnection(FakeSocket([b"\x81", socket_module.timeout("slow")]))

    # First receive() hits the timeout after consuming one byte of the frame header
    with pytest.raises(TradovateWebSocketError, match="mid-frame"):
        conn.receive(5.0)

    # Second receive() should find the connection already closed
    with pytest.raises(TradovateWebSocketError, match="closed"):
        conn.receive(5.0)
