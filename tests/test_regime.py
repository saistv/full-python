import math
import random
from datetime import datetime, timedelta, timezone

from full_python.models import MarketBar
from full_python.regime import (
    DailyAdx,
    attribute_trades,
    compute_session_features,
    variance_ratio,
    welch_t,
)


def test_adx_high_on_persistent_trend_low_on_chop() -> None:
    trending = DailyAdx(14)
    value = None
    for i in range(60):
        base = 100.0 + 5.0 * i
        value = trending.update(base + 3.0, base - 1.0, base + 2.0)
    assert value is not None and value > 40

    choppy = DailyAdx(14)
    value = None
    for i in range(60):
        offset = 2.0 if i % 2 == 0 else -2.0
        value = choppy.update(102.0 + offset, 98.0 + offset, 100.0 + offset)
    assert value is not None and value < 25


def test_variance_ratio_separates_trend_from_reversion() -> None:
    trending = [100.0 * math.exp(0.001 * i) for i in range(200)]
    reverting = [100.0 + (1.0 if i % 2 == 0 else -1.0) for i in range(200)]

    vr_trend = variance_ratio(trending)
    vr_revert = variance_ratio(reverting)
    assert vr_revert is not None and vr_revert < 0.5
    # A deterministic constant-drift series has near-zero return variance
    # noise; use a noisy trend for the upper side instead.
    rng = random.Random(7)
    noisy_trend = [100.0]
    momentum = 0.0
    for _ in range(400):
        momentum = 0.9 * momentum + rng.gauss(0.0005, 0.001)
        noisy_trend.append(noisy_trend[-1] * math.exp(momentum))
    vr_noisy = variance_ratio(noisy_trend)
    assert vr_noisy is not None and vr_noisy > 1.1
    assert vr_trend is None or vr_trend >= 0.0  # smoke: no crash on degenerate input


def _session_bars(day: int, closes_pattern: str, base_price: float) -> list[MarketBar]:
    """One weekday session: 60 overnight bars (4:00 UTC) + 90 RTH bars (13:30 UTC)."""
    bars = []
    # Skip weekends: 5 trading days per 7 calendar days from Monday Mar 2 2026.
    date = datetime(2026, 3, 2, tzinfo=timezone.utc) + timedelta(days=(day // 5) * 7 + day % 5)
    price = base_price
    for minute in range(60):  # overnight
        ts = (date.replace(hour=4, minute=0) + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bars.append(MarketBar(ts, "NQ", price, price + 2.0, price - 2.0, price, 10.0))
    for minute in range(90):  # RTH
        if closes_pattern == "trend":
            close = price + 1.0
        else:
            close = price + (1.5 if minute % 2 == 0 else -1.5)
        ts = (date.replace(hour=13, minute=30) + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bars.append(MarketBar(ts, "NQ", price, max(price, close) + 0.5, min(price, close) - 0.5, close, 50.0))
        price = close
    return bars


def test_session_features_use_only_prior_and_overnight_data() -> None:
    bars: list[MarketBar] = []
    for day in range(20):
        bars.extend(_session_bars(day, "chop", 20000.0 + day * 10))
    features = compute_session_features(bars)

    assert len(features) == 20
    first = features[0]
    assert first.prior_rth_close is None and first.gap_atr is None

    # Later sessions have prior-derived features; the VR of a choppy prior
    # session reads mean-reverting.
    late = features[-1]
    assert late.variance_ratio_q10 is not None and late.variance_ratio_q10 < 0.9
    assert late.tags.get("variance_ratio") == "mean_reverting"
    assert late.prior_rth_close is not None
    # Gap is measured from prior RTH close to today's RTH open.
    assert late.rth_open is not None

    # Mutating TODAY's RTH bars must not change today's features
    # (open excepted): rebuild with a different final session tail.
    modified = bars[:-50] + [
        MarketBar(b.timestamp_utc, b.symbol, b.open, b.high + 50, b.low - 50, b.close + 40, b.volume)
        for b in bars[-50:]
    ]
    features_modified = compute_session_features(modified)
    same = features_modified[-1]
    assert same.gap_atr == late.gap_atr
    assert same.variance_ratio_q10 == late.variance_ratio_q10
    assert same.adx_14 == late.adx_14


def test_attribution_buckets_and_flags_small_samples() -> None:
    features = compute_session_features(
        [bar for day in range(20) for bar in _session_bars(day, "chop", 20000.0)]
    )
    # Force two known tags for a deterministic join.
    for i, row in enumerate(features):
        row.tags["synthetic"] = "a" if i % 2 == 0 else "b"
    trades = [
        {"session_date": features[i].session_date, "net_pnl": str(100.0 if i % 2 == 0 else -50.0)}
        for i in range(20)
    ]

    report = attribute_trades(features, trades)

    buckets = report["axes"]["synthetic"]
    assert buckets["a"]["trades"] == 10
    assert buckets["a"]["net_pnl"] == 1000.0
    assert buckets["a"]["median_pnl"] == 100.0
    assert not buckets["a"]["proven_sample"]  # n=10 < 50
    assert buckets["b"]["net_pnl"] == -500.0
    assert report["total_trades"] == 20


def test_welch_t_direction_and_degenerate_cases() -> None:
    t = welch_t([10.0, 12.0, 11.0, 13.0], [1.0, 2.0, 1.5, 2.5])
    assert t is not None and t > 5
    assert welch_t([1.0], [1.0, 2.0]) is None
    assert welch_t([1.0, 1.0], [2.0, 2.0]) is None  # zero variance
