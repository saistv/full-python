from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from full_python.tradovate.auth import TradovateAuthClient, TradovateToken
from full_python.tradovate.config import TradovateCredentials
from full_python.tradovate.errors import (
    TradovateAuthError,
    TradovateRateLimitError,
    TradovateRequestError,
)
from full_python.tradovate.http import HttpRequest, HttpResponse, TradovateHttpClient


class FakeHttpTransport:
    def __init__(self, responses: List[HttpResponse]) -> None:
        self.responses = responses
        self.requests: List[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def _credentials() -> TradovateCredentials:
    return TradovateCredentials(
        username="test-user",
        password="test-password",
        app_id="FullPython",
        app_version="1.0",
        client_id=123,
        secret="test-secret",
        device_id="device-abc",
    )


def _token_payload(access_token: str = "access-token") -> dict:
    return {
        "accessToken": access_token,
        "mdAccessToken": "md-access-token",
        "userId": 456,
        "expirationTime": "2026-07-07T12:00:00Z",
    }


def test_auth_client_requests_access_token_with_credentials_payload() -> None:
    transport = FakeHttpTransport([HttpResponse(status=200, body=_token_payload())])
    http = TradovateHttpClient("https://demo.tradovateapi.com/v1", transport)
    auth = TradovateAuthClient(http, _credentials())

    token = auth.request_access_token()

    assert token == TradovateToken(
        access_token="access-token",
        md_access_token="md-access-token",
        user_id=456,
        expiration_time=datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc),
    )
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.path == "/auth/accesstokenrequest"
    assert request.url == "https://demo.tradovateapi.com/v1/auth/accesstokenrequest"
    assert request.body == {
        "name": "test-user",
        "password": "test-password",
        "appId": "FullPython",
        "appVersion": "1.0",
        "cid": 123,
        "sec": "test-secret",
        "deviceId": "device-abc",
    }


def test_http_client_post_adds_json_and_bearer_headers_and_preserves_body() -> None:
    transport = FakeHttpTransport([HttpResponse(status=200, body={"ok": True})])
    http = TradovateHttpClient(
        "https://demo.tradovateapi.com/v1/",
        transport,
        access_token="access-token",
    )
    body = {"accountId": 1, "symbol": "NQ"}

    result = http.post("order/placeorder", body)

    assert result == {"ok": True}
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.path == "/order/placeorder"
    assert request.url == "https://demo.tradovateapi.com/v1/order/placeorder"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Authorization"] == "Bearer access-token"
    assert request.body == body


def test_http_request_repr_redacts_authorization_and_auth_body() -> None:
    request = HttpRequest(
        method="POST",
        url="https://demo.tradovateapi.com/v1/auth/accesstokenrequest",
        path="/auth/accesstokenrequest",
        headers={"Authorization": "Bearer access-token", "Content-Type": "application/json"},
        body={"name": "user", "password": "secret-password", "sec": "api-secret"},
    )

    rendered = repr(request)

    assert "POST" in rendered
    assert "/auth/accesstokenrequest" in rendered
    assert "access-token" not in rendered
    assert "secret-password" not in rendered
    assert "api-secret" not in rendered


def test_http_response_repr_redacts_token_payload() -> None:
    response = HttpResponse(
        status=200,
        body={"accessToken": "access-token", "mdAccessToken": "md-token", "userId": 456},
    )

    rendered = repr(response)

    assert "200" in rendered
    assert "access-token" not in rendered
    assert "md-token" not in rendered


def test_http_client_raises_request_error_for_non_success_response() -> None:
    transport = FakeHttpTransport([HttpResponse(status=500, body={"error": "server broke"})])
    http = TradovateHttpClient("https://demo.tradovateapi.com/v1", transport)

    with pytest.raises(TradovateRequestError, match="500"):
        http.post("/order/placeorder", {"accountId": 1})


def test_http_client_raises_rate_limit_error_with_retry_details() -> None:
    transport = FakeHttpTransport(
        [
            HttpResponse(
                status=429,
                body={"p-time": "2.5", "p-ticket": "ticket-123"},
            )
        ]
    )
    http = TradovateHttpClient("https://demo.tradovateapi.com/v1", transport)

    with pytest.raises(TradovateRateLimitError, match="429") as exc_info:
        http.post("/order/placeorder", {"accountId": 1})

    assert exc_info.value.retry_after_seconds == 2.5
    assert exc_info.value.ticket == "ticket-123"


def test_auth_client_renews_access_token_with_old_token_authorization() -> None:
    transport = FakeHttpTransport([HttpResponse(status=200, body=_token_payload("renewed-token"))])
    http = TradovateHttpClient("https://demo.tradovateapi.com/v1", transport)
    auth = TradovateAuthClient(http, _credentials())
    old_token = TradovateToken(
        access_token="old-token",
        md_access_token="old-md-token",
        user_id=456,
        expiration_time=datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc),
    )

    renewed = auth.renew_access_token(old_token)

    assert renewed.access_token == "renewed-token"
    request = transport.requests[0]
    assert request.path == "/auth/renewAccessToken"
    assert request.headers["Authorization"] == "Bearer old-token"


def test_token_should_renew_when_remaining_lifetime_is_within_lead_seconds() -> None:
    now = datetime(2026, 7, 7, 11, 40, tzinfo=timezone.utc)
    token = TradovateToken(
        access_token="access-token",
        md_access_token="md-access-token",
        user_id=456,
        expiration_time=now + timedelta(minutes=20),
    )

    assert token.should_renew(now, lead_seconds=15 * 60) is False
    assert token.should_renew(now + timedelta(minutes=5), lead_seconds=15 * 60) is True


def test_missing_token_fields_raise_auth_error_naming_missing_field() -> None:
    transport = FakeHttpTransport(
        [HttpResponse(status=200, body={"accessToken": "access-token", "userId": 456})]
    )
    http = TradovateHttpClient("https://demo.tradovateapi.com/v1", transport)
    auth = TradovateAuthClient(http, _credentials())

    with pytest.raises(TradovateAuthError, match="mdAccessToken"):
        auth.request_access_token()


def test_invalid_token_values_raise_auth_error_without_coercion() -> None:
    transport = FakeHttpTransport(
        [
            HttpResponse(
                status=200,
                body={
                    "accessToken": None,
                    "mdAccessToken": "md-access-token",
                    "userId": 456,
                    "expirationTime": "2026-07-07T12:00:00Z",
                },
            )
        ]
    )
    http = TradovateHttpClient("https://demo.tradovateapi.com/v1", transport)
    auth = TradovateAuthClient(http, _credentials())

    with pytest.raises(TradovateAuthError, match="accessToken"):
        auth.request_access_token()
