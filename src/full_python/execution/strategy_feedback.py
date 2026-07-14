"""Dispatch broker-authoritative lifecycle feedback to a strategy."""
from __future__ import annotations

from collections.abc import Iterable

from full_python.execution.broker_protocol import StrategyFeedback
from full_python.models import Fill, Trade


def dispatch_strategy_feedback(strategy, feedback: Iterable[StrategyFeedback]) -> None:
    for item in feedback:
        if isinstance(item, Fill):
            callback = getattr(strategy, "on_fill", None)
        elif isinstance(item, Trade):
            callback = getattr(strategy, "on_trade_closed", None)
        else:  # pragma: no cover - the union is closed; fail loudly if widened.
            raise TypeError(f"unsupported strategy feedback: {item!r}")
        if callback is not None:
            callback(item)
