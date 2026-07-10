"""Tradovate adapter package.

Offline-tested adapter pieces for the live execution stack. Importing
this package has no side effects and never reads credentials.
"""

from full_python.tradovate.config import (
    DEMO_ENVIRONMENT,
    LIVE_ENVIRONMENT,
    TradovateAdapterConfig,
    TradovateCredentials,
    TradovateEnvironment,
    credentials_from_env,
)
from full_python.tradovate.errors import (
    TradovateAuthError,
    TradovateConfigError,
    TradovateError,
    TradovateFeedError,
    TradovateOrderDisabledError,
    TradovateOrderSafetyError,
    TradovateRateLimitError,
    TradovateRequestError,
    TradovateWebSocketError,
)

__all__ = [
    "DEMO_ENVIRONMENT",
    "LIVE_ENVIRONMENT",
    "TradovateAdapterConfig",
    "TradovateAuthError",
    "TradovateConfigError",
    "TradovateCredentials",
    "TradovateEnvironment",
    "TradovateError",
    "TradovateFeedError",
    "TradovateOrderDisabledError",
    "TradovateOrderSafetyError",
    "TradovateRateLimitError",
    "TradovateRequestError",
    "TradovateWebSocketError",
    "credentials_from_env",
]
