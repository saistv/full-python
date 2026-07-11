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
