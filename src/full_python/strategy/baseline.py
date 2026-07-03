from __future__ import annotations

from full_python.models import (
    ExitDecision,
    MarketBar,
    OrderIntent,
    SignalDecision,
    StrategyResult,
)
from full_python.strategy.config import BaselineMomentumConfig


class BaselineMomentumStrategy:
    """Placeholder long momentum-breakout used to prove the replay wiring.

    Entry: close breaks the prior N-bar high with a minimum body.
    Exit signal: close breaks the prior M-bar low (the simulation engine
    executes it only while a position is open; the frozen stop and session
    backstop handle everything else).
    """

    def __init__(self, config: BaselineMomentumConfig) -> None:
        self.config = config
        self._history: list[MarketBar] = []

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        if len(self._history) < self.config.breakout_lookback_bars:
            self._history.append(bar)
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="long",
                    reason="insufficient_history",
                    metadata={"history_bars": len(self._history)},
                )
            )

        exits = self._exit_signals(bar)
        prior_high = max(prior.high for prior in self._history[-self.config.breakout_lookback_bars :])
        body_points = abs(bar.close - bar.open)
        is_breakout = bar.close > prior_high
        body_pass = body_points >= self.config.min_body_points
        self._history.append(bar)

        if not is_breakout:
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="long",
                    reason="no_breakout",
                    metadata={"prior_high": prior_high, "close": bar.close},
                ),
                exits=exits,
            )

        if not body_pass:
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="long",
                    reason="body_too_small",
                    metadata={"body_points": body_points, "min_body_points": self.config.min_body_points},
                ),
                exits=exits,
            )

        stop_price = bar.close - self.config.stop_points
        signal = SignalDecision.accepted(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side="long",
            reason="breakout",
            metadata={"prior_high": prior_high, "stop_price": stop_price},
        )
        order_intent = OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side="buy",
            quantity=1,
            reason="breakout",
            metadata={"stop_price": stop_price, "signal_price": bar.close},
        )
        return StrategyResult(signal=signal, order_intents=(order_intent,), exits=exits)

    def _exit_signals(self, bar: MarketBar) -> tuple[ExitDecision, ...]:
        lookback = self.config.exit_lookback_bars
        if len(self._history) < lookback:
            return ()
        prior_low = min(prior.low for prior in self._history[-lookback:])
        if bar.close < prior_low:
            return (
                ExitDecision(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    reason="breakdown_exit",
                    metadata={"prior_low": prior_low, "close": bar.close},
                ),
            )
        return ()
