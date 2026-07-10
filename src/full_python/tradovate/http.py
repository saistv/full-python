from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from full_python.tradovate.errors import TradovateRateLimitError, TradovateRequestError


_SENSITIVE_KEYS = {
    "authorization",
    "password",
    "sec",
    "secret",
    "accesstoken",
    "access_token",
    "mdaccesstoken",
    "md_access_token",
}


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    path: str
    headers: Dict[str, str]
    body: Any

    def __repr__(self) -> str:
        return (
            "HttpRequest("
            f"method={self.method!r}, "
            f"url={self.url!r}, "
            f"path={self.path!r}, "
            f"headers={_redact(self.headers)!r}, "
            f"body={_redact(self.body)!r}"
            ")"
        )


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any

    def __repr__(self) -> str:
        return f"HttpResponse(status={self.status!r}, body={_redact(self.body)!r})"


class HttpTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse:
        ...


class UrllibHttpTransport:
    def send(self, request: HttpRequest) -> HttpResponse:
        data = None
        if request.body is not None:
            data = json.dumps(request.body).encode("utf-8")

        urllib_request = Request(
            request.url,
            data=data,
            headers=request.headers,
            method=request.method,
        )

        try:
            with urlopen(urllib_request, timeout=30) as response:
                return HttpResponse(
                    status=response.status,
                    body=_decode_response_body(response.read()),
                )
        except HTTPError as exc:
            return HttpResponse(
                status=exc.code,
                body=_decode_response_body(exc.read()),
            )
        except URLError as exc:
            reason = exc.reason.__class__.__name__
            raise TradovateRequestError(f"Tradovate request failed: {reason}") from exc


class TradovateHttpClient:
    def __init__(
        self,
        base_url: str,
        transport: HttpTransport,
        access_token: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.access_token = access_token

    def with_access_token(self, access_token: str) -> "TradovateHttpClient":
        return TradovateHttpClient(self.base_url, self.transport, access_token=access_token)

    def get(self, path: str) -> Any:
        return self._send("GET", path, None)

    def post(self, path: str, body: Any) -> Any:
        return self._send("POST", path, body)

    def account_list(self) -> Any:
        return self.get("/account/list")

    def account_find(self, name: str) -> Any:
        return self.get(f"/account/find?name={quote(name, safe='')}")

    def contract_find(self, name: str) -> Any:
        return self.get(f"/contract/find?name={quote(name, safe='')}")

    def order_place(self, body: Any) -> Any:
        return self.post("/order/placeorder", body)

    def order_place_oco(self, body: Any) -> Any:
        return self.post("/order/placeoco", body)

    def order_cancel(self, body: Any) -> Any:
        return self.post("/order/cancelorder", body)

    def order_modify(self, body: Any) -> Any:
        return self.post("/order/modifyorder", body)

    def order_liquidate_position(self, body: Any) -> Any:
        return self.post("/order/liquidateposition", body)

    def position_list(self) -> Any:
        return self.get("/position/list")

    def fill_list(self) -> Any:
        return self.get("/fill/list")

    def _send(self, method: str, path: str, body: Any) -> Any:
        normalized_path = path if path.startswith("/") else f"/{path}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.access_token is not None:
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
            retry_after_seconds = _rate_limit_retry_after(response.body)
            ticket = _rate_limit_ticket(response.body)
            raise TradovateRateLimitError(
                "Tradovate request failed with status 429",
                retry_after_seconds=retry_after_seconds,
                ticket=ticket,
            )
        if response.status < 200 or response.status >= 300:
            raise TradovateRequestError(f"Tradovate request failed with status {response.status}")

        return response.body


def _decode_response_body(raw_body: bytes) -> Any:
    if not raw_body:
        return None
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw_body.decode("utf-8", errors="replace")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if str(key).lower() in _SENSITIVE_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _rate_limit_retry_after(body: Any) -> Optional[float]:
    if not isinstance(body, dict):
        return None
    value = body.get("p-time")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate_limit_ticket(body: Any) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    value = body.get("p-ticket")
    if value is None:
        return None
    return str(value)
