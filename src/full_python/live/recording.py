"""Strategy wrapper that records observe-mode signals to the ledger.

With orders disabled the broker never fills, so the strategy's raw
on_bar output IS the observe-mode signal stream. LiveLoop does not
ledger intents itself (in the sim that is PositionEngine's job), so
this wrapper writes ORDER_INTENT / EXIT events -- the record the
shadow report (session_report.py) diffs against replay.

LiveLoop can suppress intents after a supervisor breach
(entries_allowed); this wrapper records the PRE-suppression stream. In
observe mode the supervisor has no daily_loss_stop, so the streams are
identical by construction.
"""
from __future__ import annotations

import logging

from full_python.events import EventLedger, EventType
from full_python.models import MarketBar, StrategyResult

logger = logging.getLogger("full_python.live")


class RecordingStrategy:
    def __init__(self, inner, ledger: EventLedger) -> None:
        self._inner = inner
        self._ledger = ledger
        self._session_pnl = 0.0
        self._daily_limit_hit = False

    def on_bar_context(self, *, session_pnl: float, daily_limit_hit: bool) -> None:
        self._session_pnl = session_pnl
        self._daily_limit_hit = daily_limit_hit
        inner_hook = getattr(self._inner, "on_bar_context", None)
        if inner_hook is not None:
            inner_hook(session_pnl=session_pnl, daily_limit_hit=daily_limit_hit)

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        result = self._inner.on_bar(bar)
        for intent in result.order_intents:
            payload = {
                "symbol": intent.symbol,
                "side": intent.side,
                "quantity": intent.quantity,
                "reason": intent.reason,
                "stop_price": intent.metadata.get("stop_price"),
            }
            self._ledger.append(
                EventType.ORDER_INTENT, timestamp_utc=bar.timestamp_utc, payload=payload
            )
            logger.info(
                "SIGNAL %s %s %dx stop=%s",
                bar.timestamp_utc, intent.side, intent.quantity, payload["stop_price"],
            )
        for exit_decision in result.exits:
            self._ledger.append(
                EventType.EXIT,
                timestamp_utc=bar.timestamp_utc,
                payload=exit_decision.to_payload(),
            )
            logger.info("EXIT %s %s", bar.timestamp_utc, exit_decision.reason)
        logger.info(
            "bar %s close=%.2f session_pnl=%.2f dll=%s",
            bar.timestamp_utc, bar.close, self._session_pnl, self._daily_limit_hit,
        )
        return result
