from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from full_python.tradovate.config import TradovateCredentials
from full_python.tradovate.errors import TradovateAuthError
from full_python.tradovate.http import TradovateHttpClient


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TradovateAuthError("expirationTime must be an ISO timestamp")

    normalized = value
    if value.endswith("Z"):
        normalized = f"{value[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TradovateAuthError("expirationTime must be an ISO timestamp") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class TradovateToken:
    access_token: str = field(repr=False)
    md_access_token: str = field(repr=False)
    user_id: int
    expiration_time: datetime

    def should_renew(self, now: datetime, lead_seconds: int = 15 * 60) -> bool:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        remaining_seconds = (self.expiration_time - now.astimezone(timezone.utc)).total_seconds()
        return remaining_seconds <= lead_seconds


class TradovateAuthClient:
    def __init__(self, http: TradovateHttpClient, credentials: TradovateCredentials) -> None:
        self.http = http
        self.credentials = credentials

    def request_access_token(self) -> TradovateToken:
        payload = {
            "name": self.credentials.username,
            "password": self.credentials.password,
            "appId": self.credentials.app_id,
            "appVersion": self.credentials.app_version,
            "cid": self.credentials.client_id,
            "sec": self.credentials.secret,
            "deviceId": self.credentials.device_id,
        }
        return _parse_token(self.http.post("/auth/accesstokenrequest", payload))

    def renew_access_token(self, token: TradovateToken) -> TradovateToken:
        authorized_http = self.http.with_access_token(token.access_token)
        return _parse_token(authorized_http.post("/auth/renewAccessToken", {}))


def _parse_token(payload: Any) -> TradovateToken:
    if not isinstance(payload, dict):
        raise TradovateAuthError("Token response must be a JSON object")

    for field_name in ("accessToken", "mdAccessToken", "userId", "expirationTime"):
        if field_name not in payload:
            raise TradovateAuthError(f"Missing token field: {field_name}")

    try:
        user_id = int(payload["userId"])
    except (TypeError, ValueError) as exc:
        raise TradovateAuthError("userId must be an integer") from exc

    return TradovateToken(
        access_token=_required_non_empty_string(payload, "accessToken"),
        md_access_token=_required_non_empty_string(payload, "mdAccessToken"),
        user_id=user_id,
        expiration_time=_parse_time(payload["expirationTime"]),
    )


def _required_non_empty_string(payload: dict, field_name: str) -> str:
    value = payload[field_name]
    if not isinstance(value, str) or value == "":
        raise TradovateAuthError(f"{field_name} must be a non-empty string")
    return value
