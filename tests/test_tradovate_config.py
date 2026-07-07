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


def test_live_order_and_flatten_flags_are_independent() -> None:
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


def test_credentials_repr_redacts_sensitive_values() -> None:
    creds = TradovateCredentials(
        username="user",
        password="super-secret-password",
        app_id="FullPython",
        app_version="1.0",
        client_id=123,
        secret="api-secret",
        device_id="device-abc",
    )

    rendered = repr(creds)

    assert "user" in rendered
    assert "super-secret-password" not in rendered
    assert "api-secret" not in rendered
    assert "device-abc" not in rendered


def test_credentials_from_env_rejects_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("TRADOVATE_"):
            monkeypatch.delenv(key, raising=False)

    with pytest.raises(TradovateConfigError, match="TRADOVATE_USERNAME"):
        credentials_from_env()


def test_credentials_from_env_rejects_blank_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADOVATE_USERNAME", "")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "pass")
    monkeypatch.setenv("TRADOVATE_APP_ID", "FullPython")
    monkeypatch.setenv("TRADOVATE_APP_VERSION", "1.0")
    monkeypatch.setenv("TRADOVATE_CLIENT_ID", "123")
    monkeypatch.setenv("TRADOVATE_SECRET", "secret")
    monkeypatch.setenv("TRADOVATE_DEVICE_ID", "device-abc")

    with pytest.raises(TradovateConfigError, match="TRADOVATE_USERNAME"):
        credentials_from_env()


def test_credentials_from_env_rejects_non_integer_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADOVATE_USERNAME", "user")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "pass")
    monkeypatch.setenv("TRADOVATE_APP_ID", "FullPython")
    monkeypatch.setenv("TRADOVATE_APP_VERSION", "1.0")
    monkeypatch.setenv("TRADOVATE_CLIENT_ID", "not-an-int")
    monkeypatch.setenv("TRADOVATE_SECRET", "secret")
    monkeypatch.setenv("TRADOVATE_DEVICE_ID", "device-abc")

    with pytest.raises(TradovateConfigError, match="TRADOVATE_CLIENT_ID"):
        credentials_from_env()
