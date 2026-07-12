from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

from full_python.tradovate.errors import TradovateConfigError
from full_python.instruments import instrument_spec


@dataclass(frozen=True)
class TradovateEnvironment:
    name: str
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
    password: str = field(repr=False)
    app_id: str
    app_version: str
    client_id: int
    secret: str = field(repr=False)
    device_id: str = field(repr=False)


@dataclass(frozen=True)
class TradovateAdapterConfig:
    environment: TradovateEnvironment
    account_spec: str
    account_id: int
    root_symbol: str = "NQ"
    order_enabled: bool = False
    flatten_enabled: bool = False
    token_renewal_lead_seconds: int = 15 * 60
    # Risk/cost model, mirroring SimulationConfig semantics. PER-INSTRUMENT:
    # NQ = 20.0 $/pt, MNQ = 2.0 $/pt -- no default, so a value can never
    # silently cross instruments. TradovateBroker refuses to construct
    # without dollar_point_value (and, when order_enabled, without
    # daily_loss_limit + flatten_enabled).
    dollar_point_value: Optional[float] = None
    commission_per_contract_round_trip: float = 0.0
    daily_loss_limit: Optional[float] = None

    def __post_init__(self) -> None:
        if self.dollar_point_value is not None and self.dollar_point_value <= 0:
            raise TradovateConfigError("dollar_point_value must be positive when set")
        if self.commission_per_contract_round_trip < 0:
            raise TradovateConfigError("commission_per_contract_round_trip must be >= 0")
        if self.daily_loss_limit is not None and self.daily_loss_limit <= 0:
            raise TradovateConfigError("daily_loss_limit must be positive when set")
        if self.dollar_point_value is not None:
            try:
                expected = instrument_spec(self.root_symbol).dollar_point_value
            except ValueError as exc:
                raise TradovateConfigError(str(exc)) from exc
            if self.dollar_point_value != expected:
                raise TradovateConfigError(
                    f"{self.root_symbol} requires dollar_point_value={expected}"
                )


def credentials_from_env(env: Optional[Mapping[str, str]] = None) -> TradovateCredentials:
    source = os.environ if env is None else env

    username = _required(source, "TRADOVATE_USERNAME")
    password = _required(source, "TRADOVATE_PASSWORD")
    app_id = _required(source, "TRADOVATE_APP_ID")
    app_version = _required(source, "TRADOVATE_APP_VERSION")
    client_id_raw = _required(source, "TRADOVATE_CLIENT_ID")
    secret = _required(source, "TRADOVATE_SECRET")
    device_id = _required(source, "TRADOVATE_DEVICE_ID")

    try:
        client_id = int(client_id_raw)
    except ValueError as exc:
        raise TradovateConfigError("TRADOVATE_CLIENT_ID must be an integer") from exc

    return TradovateCredentials(
        username=username,
        password=password,
        app_id=app_id,
        app_version=app_version,
        client_id=client_id,
        secret=secret,
        device_id=device_id,
    )


def _required(env: Mapping[str, str], key: str) -> str:
    try:
        value = env[key]
    except KeyError as exc:
        raise TradovateConfigError(f"Missing required environment variable: {key}") from exc
    if value == "":
        raise TradovateConfigError(f"Missing required environment variable: {key}")
    return value
