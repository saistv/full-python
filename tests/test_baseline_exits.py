from full_python.models import MarketBar
from full_python.strategy.baseline import BaselineMomentumStrategy
from full_python.strategy.config import BaselineMomentumConfig


def _bar(minute: int, *, low: float, close: float) -> MarketBar:
    return MarketBar(
        timestamp_utc=f"2026-06-30T13:{30 + minute:02d}:00Z",
        symbol="NQU2026",
        open=close,
        high=close + 1.0,
        low=low,
        close=close,
        volume=10.0,
    )


def test_breakdown_exit_fires_when_close_breaks_prior_lows() -> None:
    strategy = BaselineMomentumStrategy(
        BaselineMomentumConfig(breakout_lookback_bars=2, exit_lookback_bars=3)
    )
    bars = [
        _bar(0, low=99.0, close=100.0),
        _bar(1, low=99.5, close=100.5),
        _bar(2, low=99.2, close=100.2),
        _bar(3, low=98.0, close=98.5),  # close 98.5 < min prior low 99.0
    ]

    results = [strategy.on_bar(bar) for bar in bars]

    assert results[2].exits == ()
    assert len(results[3].exits) == 1
    assert results[3].exits[0].reason == "breakdown_exit"


def test_no_exit_signal_without_enough_history() -> None:
    strategy = BaselineMomentumStrategy(
        BaselineMomentumConfig(breakout_lookback_bars=2, exit_lookback_bars=5)
    )
    bars = [
        _bar(0, low=99.0, close=100.0),
        _bar(1, low=99.5, close=100.5),
        _bar(2, low=90.0, close=90.5),
    ]

    results = [strategy.on_bar(bar) for bar in bars]

    assert all(result.exits == () for result in results)
