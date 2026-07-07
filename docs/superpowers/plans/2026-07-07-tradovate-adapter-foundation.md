# Tradovate Adapter Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Plan A of the Tradovate adapter: an offline-tested foundation with config, errors, auth, REST client, WebSocket framing, finalized-minute chart feed, and a fake-transport broker skeleton that maps Tradovate state into existing `BrokerEvent`s without enabling real orders.

**Architecture:** Implement `src/full_python/tradovate/` as a vendor adapter behind existing seams. Domain logic depends on injected HTTP/WebSocket transports so tests require no credentials and no network. Live order placement remains disabled by default; real network transport, demo smoke tests, protective-order confirmation, and live enablement belong to Plan B after this foundation is green.

**Tech Stack:** Python 3.9 stdlib, existing `full_python` models/protocols, pytest. No new runtime dependency in this foundation plan.

---

## Scope

This plan implements the safe offline foundation from
`docs/superpowers/specs/2026-07-07-tradovate-adapter-design.md`.

Included:

- `tradovate/config.py`
- `tradovate/errors.py`
- `tradovate/auth.py`
- `tradovate/http.py`
- `tradovate/ws.py`
- `tradovate/feed.py`
- `tradovate/broker.py` in fake-transport/disabled-order form
- offline tests for request construction, parsing, event mapping, and safety gates

Not included:

- real WebSocket network transport
- real HTTP network smoke tests
- live/demo credentials
- actually enabling real order routing
- sub-project 4 dashboards/pilot tooling

## File Structure

- `src/full_python/tradovate/__init__.py`
  - package exports only; no side effects.
- `src/full_python/tradovate/errors.py`
  - typed exception hierarchy.
- `src/full_python/tradovate/config.py`
  - environment, credential, adapter config dataclasses and env loading.
- `src/full_python/tradovate/http.py`
  - transport protocol, request/response objects, REST client endpoint methods.
- `src/full_python/tradovate/auth.py`
  - token dataclass and token client using `TradovateHttpClient`.
- `src/full_python/tradovate/ws.py`
  - transport protocol, Tradovate frame encoder/parser, request correlation.
- `src/full_python/tradovate/feed.py`
  - chart subscription and `MarketDataFeed` implementation over `TradovateWebSocketClient`.
- `src/full_python/tradovate/broker.py`
  - initial `Broker` implementation with disabled-order gates, order/fill mapping, and fake-transport tests.
- `tests/test_tradovate_config.py`
- `tests/test_tradovate_http_auth.py`
- `tests/test_tradovate_ws.py`
- `tests/test_tradovate_feed.py`
- `tests/test_tradovate_broker.py`

---

### Task 1: Config And Errors

**Files:**
- Create: `src/full_python/tradovate/__init__.py`
- Create: `src/full_python/tradovate/errors.py`
- Create: `src/full_python/tradovate/config.py`
- Test: `tests/test_tradovate_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tradovate_config.py`:

```python
import os

import pytest

from full_python.tradovate.config import (
    DEMO_ENVIRONMENT,
    LIVE_ENVIRONMENT,
    TradovateAdapterConfig,
    TradovateCredentials,
    credentials_from_env,
)
from full_python.tradovate.errors import TradovateConfigError


def test_environment_urls_are_locked() -> None:
    assert DEMO_ENVIRONMENT.name == "demo"
    assert DEMO_ENVIRONMENT.rest_base_url == "https://demo.tradovateapi.com/v1"
    assert DEMO_ENVIRONMENT.ws_base_url == "wss://demo.tradovateapi.com/v1/websocket"
    assert DEMO_ENVIRONMENT.md_ws_base_url == "wss://md-d.tradovateapi.com/v1/websocket"
    assert LIVE_ENVIRONMENT.name == "live"
    assert LIVE_ENVIRONMENT.rest_base_url == "https://live.tradovateapi.com/v1"
    assert LIVE_ENVIRONMENT.ws_base_url == "wss://live.tradovateapi.com/v1/websocket"
    assert LIVE_ENVIRONMENT.md_ws_base_url == "wss://md.tradovateapi.com/v1/websocket"


def test_adapter_config_defaults_orders_disabled() -> None:
    cfg = TradovateAdapterConfig(environment=DEMO_ENVIRONMENT, account_spec="SIM123", account_id=456)
    assert cfg.order_enabled is False
    assert cfg.flatten_enabled is False
    assert cfg.root_symbol == "NQ"
    assert cfg.token_renewal_lead_seconds == 15 * 60


def test_live_orders_require_an_explicit_flag_pair() -> None:
    cfg = TradovateAdapterConfig(
        environment=LIVE_ENVIRONMENT,
        account_spec="LIVE123",
        account_id=789,
        order_enabled=True,
        flatten_enabled=False,
    )
    assert cfg.order_enabled is True
    assert cfg.flatten_enabled is False


def test_credentials_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADOVATE_USERNAME", "user")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "pass")
    monkeypatch.setenv("TRADOVATE_APP_ID", "FullPython")
    monkeypatch.setenv("TRADOVATE_APP_VERSION", "1.0")
    monkeypatch.setenv("TRADOVATE_CLIENT_ID", "123")
    monkeypatch.setenv("TRADOVATE_SECRET", "secret")
    monkeypatch.setenv("TRADOVATE_DEVICE_ID", "device-abc")

    creds = credentials_from_env()

    assert creds == TradovateCredentials(
        username="user",
        password="pass",
        app_id="FullPython",
        app_version="1.0",
        client_id=123,
        secret="secret",
        device_id="device-abc",
    )


def test_credentials_from_env_rejects_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("TRADOVATE_"):
            monkeypatch.delenv(key, raising=False)

    with pytest.raises(TradovateConfigError, match="TRADOVATE_USERNAME"):
        credentials_from_env()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m pytest tests/test_tradovate_config.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'full_python.tradovate'`.

- [ ] **Step 3: Implement package, errors, and config**

Create `src/full_python/tradovate/__init__.py`:

```python
"""Tradovate adapter package.

Offline-tested adapter pieces for the live execution stack. Importing
this package has no side effects and never reads credentials.
"""
```

Create `src/full_python/tradovate/errors.py`:

```python
from __future__ import annotations


class TradovateError(RuntimeError):
    pass


class TradovateConfigError(TradovateError):
    pass


class TradovateAuthError(TradovateError):
    pass


class TradovateRateLimitError(TradovateError):
    def __init__(self, message: str, *, retry_after_seconds: float | None = None, ticket: str | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.ticket = ticket


class TradovateRequestError(TradovateError):
    pass


class TradovateWebSocketError(TradovateError):
    pass


class TradovateOrderRejected(TradovateError):
    pass


class TradovateStateError(TradovateError):
    pass
```

Create `src/full_python/tradovate/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Literal

from full_python.tradovate.errors import TradovateConfigError


@dataclass(frozen=True)
class TradovateEnvironment:
    name: Literal["demo", "live"]
    rest_base_url: str
    ws_base_url: str
    md_ws_base_url: str


DEMO_ENVIRONMENT = TradovateEnvironment(
    name="demo",
    rest_base_url="https://demo.tradovateapi.com/v1",
    ws_base_url="wss://demo.tradovateapi.com/v1/websocket",
    md_ws_base_url="wss://md-d.tradovateapi.com/v1/websocket",
)

LIVE_ENVIRONMENT = TradovateEnvironment(
    name="live",
    rest_base_url="https://live.tradovateapi.com/v1",
    ws_base_url="wss://live.tradovateapi.com/v1/websocket",
    md_ws_base_url="wss://md.tradovateapi.com/v1/websocket",
)


@dataclass(frozen=True)
class TradovateCredentials:
    username: str
    password: str
    app_id: str
    app_version: str
    client_id: int
    secret: str
    device_id: str | None = None


@dataclass(frozen=True)
class TradovateAdapterConfig:
    environment: TradovateEnvironment
    account_spec: str
    account_id: int
    root_symbol: str = "NQ"
    order_enabled: bool = False
    flatten_enabled: bool = False
    websocket_timeout_seconds: float = 10.0
    token_renewal_lead_seconds: int = 15 * 60


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise TradovateConfigError(f"Missing required environment variable {name}")
    return value


def credentials_from_env() -> TradovateCredentials:
    raw_client_id = _required_env("TRADOVATE_CLIENT_ID")
    try:
        client_id = int(raw_client_id)
    except ValueError as exc:
        raise TradovateConfigError("TRADOVATE_CLIENT_ID must be an integer") from exc
    return TradovateCredentials(
        username=_required_env("TRADOVATE_USERNAME"),
        password=_required_env("TRADOVATE_PASSWORD"),
        app_id=_required_env("TRADOVATE_APP_ID"),
        app_version=_required_env("TRADOVATE_APP_VERSION"),
        client_id=client_id,
        secret=_required_env("TRADOVATE_SECRET"),
        device_id=os.environ.get("TRADOVATE_DEVICE_ID"),
    )
```

- [ ] **Step 4: Run test to verify pass**

Run:

```bash
python3 -m pytest tests/test_tradovate_config.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/full_python/tradovate/__init__.py src/full_python/tradovate/errors.py src/full_python/tradovate/config.py tests/test_tradovate_config.py
git commit -m "feat: add Tradovate adapter config"
```

---

### Task 2: HTTP Transport, REST Client, And Auth

**Files:**
- Create: `src/full_python/tradovate/http.py`
- Create: `src/full_python/tradovate/auth.py`
- Test: `tests/test_tradovate_http_auth.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tradovate_http_auth.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from full_python.tradovate.auth import TradovateAuthClient, TradovateToken
from full_python.tradovate.config import DEMO_ENVIRONMENT, TradovateCredentials
from full_python.tradovate.errors import TradovateAuthError, TradovateRateLimitError, TradovateRequestError
from full_python.tradovate.http import HttpRequest, HttpResponse, TradovateHttpClient


class FakeHttpTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("No scripted response left")
        return self.responses.pop(0)


def _creds() -> TradovateCredentials:
    return TradovateCredentials(
        username="user",
        password="pass",
        app_id="FullPython",
        app_version="1.0",
        client_id=123,
        secret="secret",
        device_id="device",
    )


def test_auth_client_requests_access_token_without_leaking_secret_in_error() -> None:
    transport = FakeHttpTransport([
        HttpResponse(
            status=200,
            body={
                "accessToken": "trade-token",
                "mdAccessToken": "md-token",
                "userId": 42,
                "expirationTime": "2026-07-07T12:00:00Z",
            },
        )
    ])
    http = TradovateHttpClient(DEMO_ENVIRONMENT.rest_base_url, transport)
    auth = TradovateAuthClient(http, _creds())

    token = auth.request_access_token()

    assert token.access_token == "trade-token"
    assert token.md_access_token == "md-token"
    assert token.user_id == 42
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.path == "/auth/accesstokenrequest"
    assert request.body["name"] == "user"
    assert request.body["password"] == "pass"
    assert request.body["appId"] == "FullPython"
    assert request.body["appVersion"] == "1.0"
    assert request.body["cid"] == 123
    assert request.body["sec"] == "secret"
    assert request.body["deviceId"] == "device"


def test_http_client_adds_bearer_header_and_json_body() -> None:
    transport = FakeHttpTransport([HttpResponse(status=200, body={"orderId": 555})])
    client = TradovateHttpClient(DEMO_ENVIRONMENT.rest_base_url, transport, access_token="token")

    result = client.post("/order/placeorder", {"symbol": "NQZ5"})

    assert result == {"orderId": 555}
    request = transport.requests[0]
    assert request.url == "https://demo.tradovateapi.com/v1/order/placeorder"
    assert request.headers["Authorization"] == "Bearer token"
    assert request.headers["Content-Type"] == "application/json"
    assert request.body == {"symbol": "NQZ5"}


def test_http_client_raises_for_non_2xx() -> None:
    transport = FakeHttpTransport([HttpResponse(status=500, body={"errorText": "server broke"})])
    client = TradovateHttpClient(DEMO_ENVIRONMENT.rest_base_url, transport)

    with pytest.raises(TradovateRequestError, match="500"):
        client.get("/account/list")


def test_http_client_raises_rate_limit_with_ticket() -> None:
    transport = FakeHttpTransport([
        HttpResponse(status=429, body={"p-ticket": "abc", "p-time": 7, "p-captcha": False})
    ])
    client = TradovateHttpClient(DEMO_ENVIRONMENT.rest_base_url, transport)

    with pytest.raises(TradovateRateLimitError) as exc:
        client.get("/account/list")
    assert exc.value.retry_after_seconds == 7
    assert exc.value.ticket == "abc"


def test_auth_renew_uses_existing_token() -> None:
    transport = FakeHttpTransport([
        HttpResponse(
            status=200,
            body={
                "accessToken": "renewed",
                "mdAccessToken": "md-renewed",
                "userId": 42,
                "expirationTime": "2026-07-07T12:30:00Z",
            },
        )
    ])
    http = TradovateHttpClient(DEMO_ENVIRONMENT.rest_base_url, transport, access_token="old")
    auth = TradovateAuthClient(http, _creds())
    old = TradovateToken(
        access_token="old",
        md_access_token="old-md",
        user_id=42,
        expiration_time=datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc),
    )

    renewed = auth.renew_access_token(old)

    assert renewed.access_token == "renewed"
    assert transport.requests[0].path == "/auth/renewAccessToken"
    assert transport.requests[0].headers["Authorization"] == "Bearer old"


def test_token_should_renew_before_lead_time() -> None:
    token = TradovateToken(
        access_token="a",
        md_access_token="m",
        user_id=1,
        expiration_time=datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc),
    )

    assert token.should_renew(datetime(2026, 7, 7, 11, 50, tzinfo=timezone.utc), lead_seconds=15 * 60)
    assert not token.should_renew(datetime(2026, 7, 7, 11, 40, tzinfo=timezone.utc), lead_seconds=15 * 60)


def test_auth_rejects_missing_token_fields() -> None:
    transport = FakeHttpTransport([HttpResponse(status=200, body={"accessToken": "x"})])
    http = TradovateHttpClient(DEMO_ENVIRONMENT.rest_base_url, transport)
    auth = TradovateAuthClient(http, _creds())

    with pytest.raises(TradovateAuthError, match="mdAccessToken"):
        auth.request_access_token()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m pytest tests/test_tradovate_http_auth.py -q
```

Expected: fail with `ModuleNotFoundError` for `full_python.tradovate.auth` or `http`.

- [ ] **Step 3: Implement HTTP client**

Create `src/full_python/tradovate/http.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from full_python.tradovate.errors import TradovateRateLimitError, TradovateRequestError


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    path: str
    headers: dict[str, str]
    body: dict[str, Any] | None = None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any


class HttpTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


class UrllibHttpTransport:
    def send(self, request: HttpRequest) -> HttpResponse:
        raw_body = None
        if request.body is not None:
            raw_body = json.dumps(request.body).encode("utf-8")
        req = urllib_request.Request(
            request.url,
            data=raw_body,
            headers=request.headers,
            method=request.method,
        )
        try:
            with urllib_request.urlopen(req, timeout=20) as response:
                payload = response.read().decode("utf-8")
                return HttpResponse(status=response.status, body=json.loads(payload) if payload else None)
        except HTTPError as exc:
            payload = exc.read().decode("utf-8")
            try:
                body = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                body = {"errorText": payload}
            return HttpResponse(status=exc.code, body=body)
        except URLError as exc:
            raise TradovateRequestError(f"HTTP transport error: {exc}") from exc


class TradovateHttpClient:
    def __init__(
        self,
        base_url: str,
        transport: HttpTransport,
        *,
        access_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.access_token = access_token

    def with_access_token(self, access_token: str) -> "TradovateHttpClient":
        return TradovateHttpClient(self.base_url, self.transport, access_token=access_token)

    def get(self, path: str) -> Any:
        return self._send("GET", path, None)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._send("POST", path, body or {})

    def _send(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        normalized_path = path if path.startswith("/") else f"/{path}"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        response = self.transport.send(
            HttpRequest(
                method=method,
                url=f"{self.base_url}{normalized_path}",
                path=normalized_path,
                headers=headers,
                body=body,
            )
        )
        if response.status == 429:
            payload = response.body if isinstance(response.body, dict) else {}
            raise TradovateRateLimitError(
                "Tradovate rate limit/time penalty",
                retry_after_seconds=payload.get("p-time"),
                ticket=payload.get("p-ticket"),
            )
        if response.status < 200 or response.status >= 300:
            raise TradovateRequestError(f"Tradovate HTTP {response.status}: {response.body!r}")
        return response.body

    def account_list(self) -> Any:
        return self.get("/account/list")

    def account_find(self, name: str) -> Any:
        return self.get(f"/account/find?name={quote(name, safe='')}")

    def contract_find(self, name: str) -> Any:
        return self.get(f"/contract/find?name={quote(name, safe='')}")

    def order_place(self, body: dict[str, Any]) -> Any:
        return self.post("/order/placeorder", body)

    def order_place_oco(self, body: dict[str, Any]) -> Any:
        return self.post("/order/placeoco", body)

    def order_cancel(self, body: dict[str, Any]) -> Any:
        return self.post("/order/cancelorder", body)

    def order_modify(self, body: dict[str, Any]) -> Any:
        return self.post("/order/modifyorder", body)

    def order_liquidate_position(self, body: dict[str, Any]) -> Any:
        return self.post("/order/liquidateposition", body)

    def position_list(self) -> Any:
        return self.get("/position/list")

    def fill_list(self) -> Any:
        return self.get("/fill/list")
```

- [ ] **Step 4: Implement auth client**

Create `src/full_python/tradovate/auth.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from full_python.tradovate.config import TradovateCredentials
from full_python.tradovate.errors import TradovateAuthError
from full_python.tradovate.http import TradovateHttpClient


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


@dataclass(frozen=True)
class TradovateToken:
    access_token: str
    md_access_token: str
    user_id: int
    expiration_time: datetime

    def should_renew(self, now: datetime, *, lead_seconds: int) -> bool:
        remaining = (self.expiration_time - now.astimezone(timezone.utc)).total_seconds()
        return remaining <= lead_seconds


class TradovateAuthClient:
    def __init__(self, http: TradovateHttpClient, credentials: TradovateCredentials) -> None:
        self.http = http
        self.credentials = credentials

    def request_access_token(self) -> TradovateToken:
        payload: dict[str, Any] = {
            "name": self.credentials.username,
            "password": self.credentials.password,
            "appId": self.credentials.app_id,
            "appVersion": self.credentials.app_version,
            "cid": self.credentials.client_id,
            "sec": self.credentials.secret,
        }
        if self.credentials.device_id:
            payload["deviceId"] = self.credentials.device_id
        return self._parse_token(self.http.post("/auth/accesstokenrequest", payload))

    def renew_access_token(self, token: TradovateToken) -> TradovateToken:
        renewed_http = self.http.with_access_token(token.access_token)
        return self._parse_token(renewed_http.post("/auth/renewAccessToken", {}))

    def _parse_token(self, payload: Any) -> TradovateToken:
        if not isinstance(payload, dict):
            raise TradovateAuthError("Token response was not a JSON object")
        required = ("accessToken", "mdAccessToken", "userId", "expirationTime")
        for key in required:
            if key not in payload:
                raise TradovateAuthError(f"Token response missing {key}")
        return TradovateToken(
            access_token=str(payload["accessToken"]),
            md_access_token=str(payload["mdAccessToken"]),
            user_id=int(payload["userId"]),
            expiration_time=_parse_time(str(payload["expirationTime"])),
        )
```

- [ ] **Step 5: Run tests to verify pass**

Run:

```bash
python3 -m pytest tests/test_tradovate_http_auth.py -q
```

Expected: `7 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/full_python/tradovate/http.py src/full_python/tradovate/auth.py tests/test_tradovate_http_auth.py
git commit -m "feat: add Tradovate auth and REST client"
```

---

### Task 3: WebSocket Framing With Fake Transport

**Files:**
- Create: `src/full_python/tradovate/ws.py`
- Test: `tests/test_tradovate_ws.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tradovate_ws.py`:

```python
import pytest

from full_python.tradovate.errors import TradovateWebSocketError
from full_python.tradovate.ws import (
    TradovateWebSocketClient,
    WebSocketMessage,
    encode_request,
    parse_message,
)


class FakeWebSocketTransport:
    def __init__(self, inbound: list[str]) -> None:
        self.inbound = list(inbound)
        self.sent: list[str] = []

    def send(self, frame: str) -> None:
        self.sent.append(frame)

    def receive(self, timeout_seconds: float) -> str | None:
        if not self.inbound:
            return None
        return self.inbound.pop(0)

    def close(self) -> None:
        self.sent.append("<close>")


def test_encode_request_uses_tradovate_framing() -> None:
    frame = encode_request("md/getChart", 12, {"symbol": "NQZ5"})
    assert frame == 'md/getChart\\n12\\n\\n{"symbol":"NQZ5"}'


def test_parse_response_message() -> None:
    msg = parse_message('o\\n')
    assert msg.kind == "open"

    msg = parse_message('a[{"s":200,"i":3,"d":{"ok":true}}]')
    assert msg.kind == "array"
    assert msg.payload == [{"s": 200, "i": 3, "d": {"ok": True}}]


def test_parse_event_message() -> None:
    msg = parse_message('a[{"e":"chart","d":{"charts":[]}}]')
    assert msg.kind == "array"
    assert msg.payload[0]["e"] == "chart"


def test_client_authorizes_socket() -> None:
    transport = FakeWebSocketTransport(['a[{"s":200,"i":0,"d":{}}]'])
    client = TradovateWebSocketClient(transport)

    client.authorize("token")

    assert transport.sent[0] == "authorize\\n0\\n\\ntoken"


def test_client_request_correlates_response_id() -> None:
    transport = FakeWebSocketTransport(['a[{"s":200,"i":1,"d":{"historicalId":5,"realtimeId":6}}]'])
    client = TradovateWebSocketClient(transport)

    result = client.request("md/getChart", {"symbol": "NQZ5"})

    assert result == {"historicalId": 5, "realtimeId": 6}
    assert transport.sent[0].startswith("md/getChart\\n1\\n\\n")


def test_client_raises_on_error_response() -> None:
    transport = FakeWebSocketTransport(['a[{"s":400,"i":1,"d":{"errorText":"bad"}}]'])
    client = TradovateWebSocketClient(transport)

    with pytest.raises(TradovateWebSocketError, match="400"):
        client.request("md/getChart", {"symbol": "NQZ5"})


def test_client_receive_event_skips_heartbeat() -> None:
    transport = FakeWebSocketTransport(["h", 'a[{"e":"chart","d":{"charts":[]}}]'])
    client = TradovateWebSocketClient(transport)

    event = client.receive_event(timeout_seconds=1.0)

    assert event == {"e": "chart", "d": {"charts": []}}
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m pytest tests/test_tradovate_ws.py -q
```

Expected: fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement WebSocket framing**

Create `src/full_python/tradovate/ws.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol

from full_python.tradovate.errors import TradovateWebSocketError


@dataclass(frozen=True)
class WebSocketMessage:
    kind: str
    payload: Any = None


class WebSocketTransport(Protocol):
    def send(self, frame: str) -> None: ...
    def receive(self, timeout_seconds: float) -> str | None: ...
    def close(self) -> None: ...


def encode_request(endpoint: str, request_id: int, payload: Any) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    return f"{endpoint}\\n{request_id}\\n\\n{body}"


def parse_message(frame: str) -> WebSocketMessage:
    if frame == "o" or frame == "o\\n":
        return WebSocketMessage(kind="open")
    if frame == "h":
        return WebSocketMessage(kind="heartbeat")
    if frame.startswith("a"):
        return WebSocketMessage(kind="array", payload=json.loads(frame[1:]))
    if frame.startswith("c"):
        return WebSocketMessage(kind="close", payload=frame)
    raise TradovateWebSocketError(f"Unknown websocket frame: {frame!r}")


class TradovateWebSocketClient:
    def __init__(self, transport: WebSocketTransport) -> None:
        self.transport = transport
        self._next_id = 1

    def authorize(self, token: str) -> None:
        self.transport.send(encode_request("authorize", 0, token))
        response = self._next_response(expected_id=0)
        if response.get("s") != 200:
            raise TradovateWebSocketError(f"WebSocket authorize failed: {response!r}")

    def request(self, endpoint: str, payload: dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        self.transport.send(encode_request(endpoint, request_id, payload))
        response = self._next_response(expected_id=request_id)
        status = response.get("s")
        if status != 200:
            raise TradovateWebSocketError(f"WebSocket request {endpoint} failed with {status}: {response!r}")
        return response.get("d")

    def receive_event(self, timeout_seconds: float) -> dict[str, Any] | None:
        while True:
            frame = self.transport.receive(timeout_seconds)
            if frame is None:
                return None
            message = parse_message(frame)
            if message.kind in ("open", "heartbeat"):
                continue
            if message.kind == "array":
                for item in message.payload:
                    if isinstance(item, dict) and "e" in item:
                        return item
                continue
            if message.kind == "close":
                raise TradovateWebSocketError(f"WebSocket closed: {message.payload!r}")

    def close(self) -> None:
        self.transport.close()

    def _next_response(self, *, expected_id: int) -> dict[str, Any]:
        while True:
            frame = self.transport.receive(timeout_seconds=10.0)
            if frame is None:
                raise TradovateWebSocketError(f"Timed out waiting for response {expected_id}")
            message = parse_message(frame)
            if message.kind in ("open", "heartbeat"):
                continue
            if message.kind != "array":
                raise TradovateWebSocketError(f"Unexpected frame while waiting for response: {message!r}")
            for item in message.payload:
                if isinstance(item, dict) and item.get("i") == expected_id:
                    return item
```

- [ ] **Step 4: Run test to verify pass**

Run:

```bash
python3 -m pytest tests/test_tradovate_ws.py -q
```

Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/full_python/tradovate/ws.py tests/test_tradovate_ws.py
git commit -m "feat: add Tradovate websocket framing"
```

---

### Task 4: Market Data Feed From Chart Bars

**Files:**
- Create: `src/full_python/tradovate/feed.py`
- Test: `tests/test_tradovate_feed.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tradovate_feed.py`:

```python
from full_python.livedata.feed import VendorBar
from full_python.tradovate.feed import TradovateMarketDataFeed, chart_bar_to_vendor_bar


class FakeWsClient:
    def __init__(self, events):
        self.events = list(events)
        self.requests = []

    def request(self, endpoint, payload):
        self.requests.append((endpoint, payload))
        if endpoint == "md/getChart":
            return {"historicalId": 10, "realtimeId": 11}
        if endpoint == "md/cancelChart":
            return {}
        raise AssertionError(endpoint)

    def receive_event(self, timeout_seconds):
        if not self.events:
            return None
        return self.events.pop(0)


def test_chart_bar_to_vendor_bar_combines_up_down_volume() -> None:
    bar = chart_bar_to_vendor_bar(
        symbol="NQZ5",
        raw={
            "timestamp": "2026-07-07T14:31:00.000Z",
            "open": 20000.0,
            "high": 20005.0,
            "low": 19995.0,
            "close": 20001.0,
            "upVolume": 12.5,
            "downVolume": 7.5,
        },
    )

    assert bar == VendorBar(
        symbol="NQZ5",
        timestamp_utc="2026-07-07T14:31:00Z",
        open=20000.0,
        high=20005.0,
        low=19995.0,
        close=20001.0,
        volume=20.0,
    )


def test_feed_subscribes_to_one_minute_chart() -> None:
    ws = FakeWsClient([])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")

    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=5)

    endpoint, payload = ws.requests[0]
    assert endpoint == "md/getChart"
    assert payload["symbol"] == "NQZ5"
    assert payload["chartDescription"]["underlyingType"] == "MinuteBar"
    assert payload["chartDescription"]["elementSize"] == 1
    assert payload["chartDescription"]["elementSizeUnit"] == "UnderlyingUnits"
    assert payload["timeRange"]["closestTimestamp"] == "2026-07-07T14:31Z"
    assert payload["timeRange"]["asMuchAsElements"] == 5


def test_next_bar_returns_chart_bars_and_deduplicates() -> None:
    ws = FakeWsClient([
        {
            "e": "chart",
            "d": {
                "charts": [
                    {"id": 11, "bars": [
                        {"timestamp": "2026-07-07T14:31:00.000Z", "open": 1, "high": 2, "low": 0, "close": 1.5, "upVolume": 3, "downVolume": 4},
                        {"timestamp": "2026-07-07T14:31:00.000Z", "open": 1, "high": 2, "low": 0, "close": 1.5, "upVolume": 3, "downVolume": 4},
                        {"timestamp": "2026-07-07T14:32:00.000Z", "open": 2, "high": 3, "low": 1, "close": 2.5, "upVolume": 1, "downVolume": 1},
                    ]}
                ]
            },
        }
    ])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=5)

    first = feed.next_bar(timeout_seconds=1.0)
    second = feed.next_bar(timeout_seconds=1.0)
    third = feed.next_bar(timeout_seconds=1.0)

    assert first.timestamp_utc == "2026-07-07T14:31:00Z"
    assert second.timestamp_utc == "2026-07-07T14:32:00Z"
    assert third is None


def test_cancel_uses_realtime_subscription_id() -> None:
    ws = FakeWsClient([])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=5)

    feed.cancel()

    assert ws.requests[-1] == ("md/cancelChart", {"subscriptionId": 11})
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m pytest tests/test_tradovate_feed.py -q
```

Expected: fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement chart feed**

Create `src/full_python/tradovate/feed.py`:

```python
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Protocol

from full_python.livedata.feed import MarketDataFeed, VendorBar


class ChartWebSocketClient(Protocol):
    def request(self, endpoint: str, payload: dict[str, Any]) -> Any: ...
    def receive_event(self, timeout_seconds: float) -> dict[str, Any] | None: ...


def _normalize_timestamp(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def chart_bar_to_vendor_bar(*, symbol: str, raw: dict[str, Any]) -> VendorBar:
    volume = raw.get("volume")
    if volume is None:
        volume = float(raw.get("upVolume", 0.0)) + float(raw.get("downVolume", 0.0))
    return VendorBar(
        symbol=symbol,
        timestamp_utc=_normalize_timestamp(str(raw["timestamp"])),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        volume=float(volume),
    )


class TradovateMarketDataFeed(MarketDataFeed):
    def __init__(self, ws: ChartWebSocketClient, *, symbol: str) -> None:
        self.ws = ws
        self.symbol = symbol
        self.historical_id: int | None = None
        self.realtime_id: int | None = None
        self._queue: Deque[VendorBar] = deque()
        self._seen_timestamps: set[str] = set()

    def subscribe(self, *, closest_timestamp: str, bars_back: int) -> None:
        response = self.ws.request(
            "md/getChart",
            {
                "symbol": self.symbol,
                "chartDescription": {
                    "underlyingType": "MinuteBar",
                    "elementSize": 1,
                    "elementSizeUnit": "UnderlyingUnits",
                    "withHistogram": False,
                },
                "timeRange": {
                    "closestTimestamp": closest_timestamp,
                    "asMuchAsElements": bars_back,
                },
            },
        )
        self.historical_id = int(response["historicalId"])
        self.realtime_id = int(response["realtimeId"])

    def next_bar(self, timeout_seconds: float) -> VendorBar | None:
        if self._queue:
            return self._queue.popleft()
        event = self.ws.receive_event(timeout_seconds)
        if event is None:
            return None
        if event.get("e") != "chart":
            return None
        for chart in event.get("d", {}).get("charts", []):
            chart_id = int(chart.get("id"))
            if chart_id not in {self.historical_id, self.realtime_id}:
                continue
            for raw in chart.get("bars", []):
                bar = chart_bar_to_vendor_bar(symbol=self.symbol, raw=raw)
                if bar.timestamp_utc in self._seen_timestamps:
                    continue
                self._seen_timestamps.add(bar.timestamp_utc)
                self._queue.append(bar)
        if self._queue:
            return self._queue.popleft()
        return None

    def cancel(self) -> None:
        if self.realtime_id is not None:
            self.ws.request("md/cancelChart", {"subscriptionId": self.realtime_id})
```

- [ ] **Step 4: Run test to verify pass**

Run:

```bash
python3 -m pytest tests/test_tradovate_feed.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/full_python/tradovate/feed.py tests/test_tradovate_feed.py
git commit -m "feat: add Tradovate chart market data feed"
```

---

### Task 5: Broker Skeleton, Disabled Gates, And Event Mapping

**Files:**
- Create: `src/full_python/tradovate/broker.py`
- Test: `tests/test_tradovate_broker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tradovate_broker.py`:

```python
import pytest

from full_python.data.sessions import classify_timestamp
from full_python.execution.broker_protocol import Acked, BrokerPosition, Filled, PartialFilled, Rejected
from full_python.models import MarketBar, OrderIntent, StrategyResult
from full_python.tradovate.broker import TradovateBroker, TradovateRawEvent
from full_python.tradovate.config import DEMO_ENVIRONMENT, TradovateAdapterConfig
from full_python.tradovate.errors import TradovateStateError


class FakeRestClient:
    def __init__(self) -> None:
        self.placed = []
        self.oco = []
        self.liquidations = []

    def order_place(self, body):
        self.placed.append(body)
        return {"orderId": len(self.placed) + 100}

    def order_place_oco(self, body):
        self.oco.append(body)
        return {"orderId": 900, "ocoId": 901}

    def order_liquidate_position(self, body):
        self.liquidations.append(body)
        return {"ok": True}


def _cfg(order_enabled=False, flatten_enabled=False):
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT,
        account_spec="SIM123",
        account_id=456,
        order_enabled=order_enabled,
        flatten_enabled=flatten_enabled,
    )


def _bar():
    return MarketBar("2026-07-07T14:31:00Z", "NQZ5", 100, 101, 99, 100.5, 10)


def _buy_result():
    return StrategyResult(order_intents=(OrderIntent.market_entry(
        timestamp_utc="2026-07-07T14:31:00Z",
        symbol="NQZ5",
        side="buy",
        quantity=1,
        reason="adaptive_trend",
        metadata={"stop_price": 90.0, "signal_price": 100.5},
    ),))


def test_order_disabled_rejects_intent_without_rest_call() -> None:
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=False), rest)
    bar = _bar()

    broker.apply_strategy_result(bar, classify_timestamp(bar.timestamp_utc), _buy_result())

    events = broker.poll_events()
    assert len(rest.placed) == 0
    assert isinstance(events[0], Rejected)
    assert events[0].reason == "order_disabled"


def test_market_order_enabled_places_automated_order_and_acks() -> None:
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True), rest)
    bar = _bar()

    broker.apply_strategy_result(bar, classify_timestamp(bar.timestamp_utc), _buy_result())

    assert rest.placed[0]["accountSpec"] == "SIM123"
    assert rest.placed[0]["accountId"] == 456
    assert rest.placed[0]["action"] == "Buy"
    assert rest.placed[0]["symbol"] == "NQZ5"
    assert rest.placed[0]["orderQty"] == 1
    assert rest.placed[0]["orderType"] == "Market"
    assert rest.placed[0]["isAutomated"] is True
    assert broker.poll_events() == [Acked(order_id="101")]


def test_live_enabled_entry_requires_stop_price() -> None:
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True), rest)
    bar = _bar()
    result = StrategyResult(order_intents=(OrderIntent.market_entry(
        timestamp_utc=bar.timestamp_utc,
        symbol=bar.symbol,
        side="buy",
        quantity=1,
        reason="missing_stop",
        metadata={},
    ),))

    with pytest.raises(TradovateStateError, match="stop_price"):
        broker.apply_strategy_result(bar, classify_timestamp(bar.timestamp_utc), result)


def test_fill_event_updates_position_and_emits_filled() -> None:
    broker = TradovateBroker(_cfg(order_enabled=False), FakeRestClient())

    broker.ingest_raw_event(TradovateRawEvent(kind="fill", data={
        "orderId": 77,
        "action": "Buy",
        "qty": 1,
        "price": 100.25,
        "timestamp": "2026-07-07T14:32:00Z",
        "reason": "adaptive_trend",
    }))

    assert broker.position == BrokerPosition(side="long", quantity=1, entry_price=100.25)
    assert broker.poll_events() == [Filled(
        order_id="77",
        side="buy",
        quantity=1,
        price=100.25,
        timestamp_utc="2026-07-07T14:32:00Z",
        reason="adaptive_trend",
    )]


def test_partial_fill_maps_to_partial_filled_event() -> None:
    broker = TradovateBroker(_cfg(order_enabled=False), FakeRestClient())

    broker.ingest_raw_event(TradovateRawEvent(kind="partial_fill", data={
        "orderId": 77,
        "action": "Buy",
        "qty": 1,
        "remaining": 2,
        "price": 100.25,
        "timestamp": "2026-07-07T14:32:00Z",
    }))

    event = broker.poll_events()[0]
    assert isinstance(event, PartialFilled)
    assert event.order_id == "77"
    assert event.remaining == 2


def test_flatten_disabled_raises_without_liquidation() -> None:
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(flatten_enabled=False), rest)

    with pytest.raises(TradovateStateError, match="flatten_disabled"):
        broker.flatten(_bar(), "data_outage")
    assert rest.liquidations == []


def test_flatten_enabled_uses_liquidate_position() -> None:
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(flatten_enabled=True), rest)
    broker.ingest_raw_event(TradovateRawEvent(kind="position", data={
        "side": "long",
        "qty": 1,
        "price": 100.25,
    }))

    broker.flatten(_bar(), "data_outage")

    assert rest.liquidations == [{
        "accountSpec": "SIM123",
        "accountId": 456,
        "symbol": "NQZ5",
        "admin": False,
    }]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m pytest tests/test_tradovate_broker.py -q
```

Expected: fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement broker skeleton**

Create `src/full_python/tradovate/broker.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from full_python.data.sessions import SessionInfo
from full_python.execution.broker_protocol import (
    Acked,
    BrokerEvent,
    BrokerPosition,
    Filled,
    PartialFilled,
    Rejected,
)
from full_python.models import MarketBar, StrategyResult, Trade
from full_python.tradovate.config import TradovateAdapterConfig
from full_python.tradovate.errors import TradovateStateError


@dataclass(frozen=True)
class TradovateRawEvent:
    kind: str
    data: dict[str, Any]


def _action_to_side(action: str) -> str:
    normalized = action.lower()
    if normalized == "buy":
        return "buy"
    if normalized == "sell":
        return "sell"
    raise TradovateStateError(f"Unknown Tradovate action {action!r}")


def _position_side_from_fill(side: str) -> str:
    return "long" if side == "buy" else "short"


class TradovateBroker:
    def __init__(self, config: TradovateAdapterConfig, rest_client) -> None:
        self.config = config
        self.rest = rest_client
        self._events: list[BrokerEvent] = []
        self._position: Optional[BrokerPosition] = None
        self._trades: list[Trade] = []
        self._daily_limit_hit = False

    def process_bar_open(self, bar: MarketBar, session: SessionInfo) -> float:
        return 0.0

    def apply_strategy_result(
        self, bar: MarketBar, session: SessionInfo, result: StrategyResult
    ) -> None:
        for intent in result.order_intents:
            if not self.config.order_enabled:
                self._events.append(Rejected(order_id=f"disabled-{len(self._events)+1}", reason="order_disabled"))
                continue
            if "stop_price" not in intent.metadata:
                raise TradovateStateError("live-enabled Tradovate entry requires stop_price metadata")
            action = "Buy" if intent.side == "buy" else "Sell"
            response = self.rest.order_place({
                "accountSpec": self.config.account_spec,
                "accountId": self.config.account_id,
                "action": action,
                "symbol": intent.symbol,
                "orderQty": intent.quantity,
                "orderType": "Market",
                "isAutomated": True,
            })
            self._events.append(Acked(order_id=str(response["orderId"])))

    def note_bar_processed(self, bar: MarketBar, session: SessionInfo) -> None:
        return None

    def close_end_of_data(self) -> None:
        return None

    def flatten(self, bar: MarketBar, reason: str) -> None:
        if not self.config.flatten_enabled:
            raise TradovateStateError("flatten_disabled")
        if self._position is None:
            return
        self.rest.order_liquidate_position({
            "accountSpec": self.config.account_spec,
            "accountId": self.config.account_id,
            "symbol": bar.symbol,
            "admin": False,
        })

    def ingest_raw_event(self, event: TradovateRawEvent) -> None:
        if event.kind == "position":
            self._position = BrokerPosition(
                side=str(event.data["side"]),
                quantity=int(event.data["qty"]),
                entry_price=float(event.data["price"]),
            )
            return
        if event.kind == "partial_fill":
            side = _action_to_side(str(event.data["action"]))
            self._events.append(PartialFilled(
                order_id=str(event.data["orderId"]),
                side=side,
                quantity=int(event.data["qty"]),
                remaining=int(event.data["remaining"]),
                price=float(event.data["price"]),
                timestamp_utc=str(event.data["timestamp"]),
            ))
            return
        if event.kind == "fill":
            side = _action_to_side(str(event.data["action"]))
            qty = int(event.data["qty"])
            price = float(event.data["price"])
            self._position = BrokerPosition(
                side=_position_side_from_fill(side), quantity=qty, entry_price=price
            )
            self._events.append(Filled(
                order_id=str(event.data["orderId"]),
                side=side,
                quantity=qty,
                price=price,
                timestamp_utc=str(event.data["timestamp"]),
                reason=str(event.data.get("reason", "tradovate_fill")),
            ))
            return
        raise TradovateStateError(f"Unknown raw Tradovate event kind {event.kind!r}")

    def poll_events(self) -> list[BrokerEvent]:
        events = list(self._events)
        self._events.clear()
        return events

    @property
    def position(self) -> Optional[BrokerPosition]:
        return self._position

    @property
    def trades(self) -> list[Trade]:
        return self._trades

    @property
    def daily_limit_hit(self) -> bool:
        return self._daily_limit_hit
```

- [ ] **Step 4: Run test to verify pass**

Run:

```bash
python3 -m pytest tests/test_tradovate_broker.py -q
```

Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/full_python/tradovate/broker.py tests/test_tradovate_broker.py
git commit -m "feat: add Tradovate broker safety skeleton"
```

---

### Task 6: Foundation Integration And Regression Suite

**Files:**
- Test: `tests/test_tradovate_feed.py`
- Test: `tests/test_tradovate_broker.py`
- Modify docs if implementation notes are needed: `docs/superpowers/specs/2026-07-07-tradovate-adapter-design.md`

- [ ] **Step 1: Add feed integration test through `LiveBarSource`**

Append to `tests/test_tradovate_feed.py`:

```python
from datetime import date, datetime, timezone

from full_python.livedata.contract_authority import ContractAuthority
from full_python.livedata.live_bar_source import ActiveWindow, LiveBarSource


class FakeClock:
    def __init__(self, now):
        self._now = now
    def now(self):
        return self._now


def test_tradovate_feed_integrates_with_live_bar_source() -> None:
    auth = ContractAuthority(root="NQ")
    front = auth.front_contract(date(2025, 11, 3))
    ws = FakeWsClient([
        {
            "e": "chart",
            "d": {
                "charts": [
                    {"id": 11, "bars": [
                        {"timestamp": "2025-11-03T14:31:00.000Z", "open": 1, "high": 2, "low": 0, "close": 1.5, "upVolume": 3, "downVolume": 4},
                    ]}
                ]
            },
        }
    ])
    feed = TradovateMarketDataFeed(ws, symbol=front)
    feed.subscribe(closest_timestamp="2025-11-03T14:31Z", bars_back=1)
    src = LiveBarSource(
        feed,
        FakeClock(datetime(2025, 11, 3, 14, 32, tzinfo=timezone.utc)),
        auth,
        ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=16 * 60),
        position_provider=lambda: False,
    )

    bar = next(iter(src))

    assert bar.symbol == front
    assert bar.timestamp_utc == "2025-11-03T14:31:00Z"
    assert bar.volume == 7.0
```

- [ ] **Step 2: Add broker/state-machine partial-fill integration test**

Append to `tests/test_tradovate_broker.py`:

```python
import pytest

from full_python.execution.state_machine import ExecutionInvariantError, OrderStateMachine


def test_partial_fill_event_is_fatal_to_existing_state_machine() -> None:
    broker = TradovateBroker(_cfg(order_enabled=False), FakeRestClient())
    broker.ingest_raw_event(TradovateRawEvent(kind="partial_fill", data={
        "orderId": 77,
        "action": "Buy",
        "qty": 1,
        "remaining": 2,
        "price": 100.25,
        "timestamp": "2026-07-07T14:32:00Z",
    }))
    machine = OrderStateMachine()

    with pytest.raises(ExecutionInvariantError, match="partial fill not modeled"):
        for event in broker.poll_events():
            machine.on_event(event)
```

- [ ] **Step 3: Run focused Tradovate tests**

Run:

```bash
python3 -m pytest tests/test_tradovate_config.py tests/test_tradovate_http_auth.py tests/test_tradovate_ws.py tests/test_tradovate_feed.py tests/test_tradovate_broker.py -q
```

Expected: all Tradovate tests pass.

- [ ] **Step 4: Run full suite**

Run:

```bash
python3 -m pytest -q
```

Expected: full project suite passes.

- [ ] **Step 5: Commit integration tests if changed**

```bash
git add tests/test_tradovate_feed.py tests/test_tradovate_broker.py
git commit -m "test: cover Tradovate adapter integration safety"
```

If Step 5 has nothing to commit because the integration tests were included in earlier task commits, skip this commit and record that in the final summary.

---

## Plan Self-Review

Spec coverage:

- Config/errors: Task 1.
- Auth/REST: Task 2.
- WebSocket framing: Task 3.
- Market-data `MarketDataFeed`: Task 4 and Task 6.
- Broker skeleton and event mapping: Task 5 and Task 6.
- Offline fake transports: Tasks 2, 3, 4, 5.
- Live-order disabled by default: Task 1 and Task 5.
- Partial fills fatal: Task 5 and Task 6.

Explicitly deferred to Plan B:

- real WebSocket network transport dependency;
- real demo credential smoke test;
- broker-held protective stop/OCO confirmation after a real fill;
- complete closed-trade reconstruction from broker fills;
- live/demo order enablement checklist.

This deferral is intentional because the spec covers multiple independent subsystems. This foundation plan creates a tested adapter core first; Plan B should extend it from fake-transport foundation to real demo connectivity and protective-order confirmation.
