from full_python.models import MarketBar
from full_python.strategy.baseline import BaselineMomentumStrategy
from full_python.strategy.config import BaselineMomentumConfig


def test_baseline_strategy_rejects_until_enough_history_exists() -> None:
    strategy = BaselineMomentumStrategy(BaselineMomentumConfig())
    bar = MarketBar(
        timestamp_utc="2026-06-30T13:30:00Z",
        symbol="NQU2026",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
    )

    result = strategy.on_bar(bar)

    assert result.signal is not None
    assert result.signal.decision == "rejected"
    assert result.signal.reason == "insufficient_history"
    assert result.order_intents == ()


def test_baseline_strategy_accepts_breakout_after_history() -> None:
    strategy = BaselineMomentumStrategy(BaselineMomentumConfig(breakout_lookback_bars=2))
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 100, 101, 99, 100, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100, 102, 99, 101, 10),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 101, 103, 100, 102.5, 10),
    ]

    first = strategy.on_bar(bars[0])
    second = strategy.on_bar(bars[1])
    third = strategy.on_bar(bars[2])

    assert first.signal.reason == "insufficient_history"
    assert second.signal.reason == "insufficient_history"
    assert third.signal.decision == "accepted"
    assert third.signal.side == "long"
    assert third.order_intents[0].side == "buy"
    assert third.order_intents[0].metadata["stop_price"] == 72.5
