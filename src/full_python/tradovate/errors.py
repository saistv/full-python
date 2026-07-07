from __future__ import annotations

from typing import Optional


class TradovateError(Exception):
    pass


class TradovateConfigError(TradovateError):
    pass


class TradovateAuthError(TradovateError):
    pass


class TradovateRequestError(TradovateError):
    pass


class TradovateRateLimitError(TradovateRequestError):
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: Optional[float] = None,
        ticket: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.ticket = ticket


class TradovateWebSocketError(TradovateError):
    pass


class TradovateFeedError(TradovateError):
    pass


class TradovateOrderDisabledError(TradovateError):
    pass


class TradovateOrderSafetyError(TradovateError):
    pass
