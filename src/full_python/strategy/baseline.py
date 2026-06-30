from __future__ import annotations

from full_python.models import MarketBar, OrderIntent, SignalDecision, StrategyResult
from full_python.strategy.config import BaselineMomentumConfig


class BaselineMomentumStrategy:
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
                )
            )

        if not body_pass:
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="long",
                    reason="body_too_small",
                    metadata={"body_points": body_points, "min_body_points": self.config.min_body_points},
                )
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
            metadata={"stop_price": stop_price},
        )
        return StrategyResult(signal=signal, order_intents=(order_intent,))
