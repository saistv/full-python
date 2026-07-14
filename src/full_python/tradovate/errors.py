from __future__ import annotations

from typing import Optional

from full_python.execution.state_machine import ExecutionInvariantError
from full_python.livedata.errors import LiveDataError


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


class TradovateFeedError(TradovateError, LiveDataError):
    """The market-data feed can no longer be trusted.

    Subclasses LiveDataError so LiveLoop's existing data-outage path catches it:
    flatten (the broker is still authoritative on a data loss) and halt. Without
    this it was neither a LiveDataError nor an ExecutionInvariantError, so a feed
    protocol failure crashed straight through the safety layer.
    """


class TradovateOrderDisabledError(TradovateError):
    pass


class TradovateOrderSafetyError(TradovateError):
    pass


class TradovateStateError(TradovateError, ExecutionInvariantError):
    """Broker/account state can no longer be proven.

    Subclasses ExecutionInvariantError so LiveLoop's existing
    invariant-halt path catches it: halt WITHOUT flatten (position truth
    unknown). Never catch-and-continue this in adapter code.
    """
