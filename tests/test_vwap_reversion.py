from datetime import datetime, timedelta, timezone

from full_python.events import EventType
from full_python.models import MarketBar
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.vwap_reversion import VwapReversionStrategy
from full_python.strategy.vwap_reversion_config import VwapReversionConfig


def test_config_is_literature_faithful_baseline() -> None:
    config = VwapReversionConfig()

    assert config.band_atr_mult >= 2.5  # principle 6: extreme entry
    assert config.stop_atr_mult <= 1.0  # principle 3: tight stop
    assert config.rr_multiple >= 2.0  # principle 2
    assert 15 <= config.time_stop_bars <= 20  # principle 4
    assert config.adx_max <= 20.0  # principle 5
    assert config.entry_start_minutes_et >= 10 * 60  # disjoint from AT window
    assert len(config.parameter_hash()) == 64


def _flat_then_spike_session(day: int, spike_at: int, spike_points: float) -> list[MarketBar]:
    """RTH session: flat tape, one vertical extension at `spike_at`, then flat.

    Flat premarket/afternoon keeps ATR tiny so the spike is many ATRs.
    Weekday-safe: day counts trading days from Monday 2026-03-02.
    """
    date = datetime(2026, 3, 2, tzinfo=timezone.utc) + timedelta(days=(day // 5) * 7 + day % 5)
    bars = []
    price = 20000.0
    for minute in range(390):
        ts = (date.replace(hour=13, minute=30) + timedelta(minutes=minute)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        if minute == spike_at:
            close = price + spike_points
        elif spike_at < minute:
            close = 20000.0 + spike_points  # hold the extension (no reversion)
        else:
            close = price + (0.25 if minute % 2 == 0 else -0.25)
        bars.append(MarketBar(ts, "NQ", price, max(price, close) + 0.25,
                              min(price, close) - 0.25, close, 100.0))
        price = close
    return bars


def _run(bars, config=None):
    strategy = VwapReversionStrategy(config or VwapReversionConfig())
    sim = SimulationConfig(
        point_value=20.0, commission_per_contract_round_trip=10.0,
        entry_slippage_points=0.0, exit_slippage_points=0.0,
        rth_open_extra_entry_slippage_points=0.0,
    )
    return SimulationEngine(sim).run(bars, strategy)


def _with_adx_warmup(session_builder, trading_days: int = 40):
    """Choppy warmup days so daily ADX(14) exists and reads non-trending."""
    bars = []
    for day in range(trading_days):
        bars.extend(session_builder(day))
    return bars


def test_fades_extreme_extension_with_2r_bracket() -> None:
    def build(day):
        if day < 35:
            # Alternating up/down days: low ADX.
            direction = 1 if day % 2 == 0 else -1
            return _flat_then_spike_session(day, spike_at=200, spike_points=12.0 * direction)
        # Test day: an upward spike after 11:00 ET held into the close.
        return _flat_then_spike_session(day, spike_at=120, spike_points=15.0)

    result = _run(_with_adx_warmup(build, 36))

    accepted = [
        r for r in result.ledger.records
        if r.event_type == EventType.SIGNAL_DECISION and r.payload.get("decision") == "accepted"
    ]
    assert accepted, "expected at least one fade signal on the spike day"
    first = accepted[0].payload
    assert first["side"] == "short"  # fades an upward extension
    assert first["extension_atr"] >= 2.5  # principle 6: extreme entry only

    # R:R geometry: the short's stop sits 1R above and target 2R below the
    # signal close, so the full bracket spans 3R (one tick of quantization
    # slack per level).
    intents = [
        r for r in result.ledger.records if r.event_type == EventType.ORDER_INTENT
    ]
    intent = intents[0].payload
    signal_price = intent["signal_price"]
    stop_dist = abs(intent["stop_price"] - signal_price)
    target_dist = abs(intent["target_price"] - signal_price)
    assert abs(target_dist - 2.0 * stop_dist) <= 0.51
    assert (intent["stop_price"] > signal_price) and (intent["target_price"] < signal_price)

    assert result.trades, "signal should have filled"
    assert result.trades[0].exit_reason in (
        "stop", "target", "time_stop", "session_flatten", "stop_gap"
    )


def test_time_stop_exits_after_configured_bars() -> None:
    def build(day):
        if day < 35:
            direction = 1 if day % 2 == 0 else -1
            return _flat_then_spike_session(day, spike_at=200, spike_points=12.0 * direction)
        # Spike then PERFECTLY FLAT at the extension: neither stop nor
        # target can hit; only the time stop can end the trade.
        return _flat_then_spike_session(day, spike_at=120, spike_points=15.0)

    config = VwapReversionConfig(time_stop_bars=20)
    result = _run(_with_adx_warmup(build, 36), config)

    time_stopped = [t for t in result.trades if t.exit_reason == "time_stop"]
    assert time_stopped, f"expected a time-stop exit, got {[t.exit_reason for t in result.trades]}"
    trade = time_stopped[0]
    entry = datetime.strptime(trade.entry_timestamp_utc, "%Y-%m-%dT%H:%M:%SZ")
    exit_ = datetime.strptime(trade.exit_timestamp_utc, "%Y-%m-%dT%H:%M:%SZ")
    held_bars = int((exit_ - entry).total_seconds() // 60)
    assert 20 <= held_bars <= 22  # 20-bar clock + next-open fill


def test_adx_gate_blocks_trending_days() -> None:
    def build(day):
        # Persistent one-directional drift every day: high daily ADX.
        date = datetime(2026, 3, 2, tzinfo=timezone.utc) + timedelta(days=(day // 5) * 7 + day % 5)
        bars = []
        price = 20000.0 + day * 200.0
        for minute in range(390):
            ts = (date.replace(hour=13, minute=30) + timedelta(minutes=minute)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            close = price + 0.5 if minute != 120 else price + 15.0  # spike too
            bars.append(MarketBar(ts, "NQ", price, max(price, close) + 0.25,
                                  min(price, close) - 0.25, close, 100.0))
            price = close
        return bars

    result = _run(_with_adx_warmup(build, 40))

    assert not result.trades, "ADX gate must block entries on trending tape"
    rejections = {
        r.payload.get("reason")
        for r in result.ledger.records
        if r.event_type == EventType.REJECTION
    }
    assert "adx_trending" in rejections
