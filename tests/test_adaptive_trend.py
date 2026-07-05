import math
import pytest
import statistics
from datetime import datetime, timedelta, timezone

from full_python.events import EventType
from full_python.models import MarketBar, Trade
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import AdaptiveTrendConfig


def test_config_defaults_are_production_values() -> None:
    config = AdaptiveTrendConfig()

    assert config.atf_length == 12
    assert config.atf_smooth == 22
    assert config.atf_sensitivity == 4.5
    assert config.sr_left_bars == 5
    assert config.sr_right_bars == 3
    assert config.prove_it_bars == 2
    assert config.wings_body_atr_frac == 0.40
    assert config.wings_close_frac == 0.65
    assert config.sr_min_stop_distance == 15.0
    assert config.max_stop_distance == 31.0
    assert config.entry_start_minutes_et == 570
    assert config.entry_end_minutes_et == 600
    assert config.contracts == 1
    assert len(config.parameter_hash()) == 64


def test_config_defaults_include_disabled_prior_vol_gate() -> None:
    config = AdaptiveTrendConfig()

    assert config.enable_prior_vol_gate is False
    assert config.prior_vol_high_threshold == pytest.approx(0.0004638315483775433)
    assert len(config.parameter_hash()) == 64


def test_long_stop_floor_cap_and_passthrough() -> None:
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())

    # Pivot close by: distance 10 < floor 15 -> re-based to close - 15.
    stop, capped = strategy._compute_long_stop(105.0, 100.0)
    assert stop == 90.0
    assert not capped

    # Pivot far away: distance 45 > cap 31 -> close - 31, flagged capped.
    stop, capped = strategy._compute_long_stop(140.0, 100.0)
    assert stop == 109.0
    assert capped

    # Normal band: pivot 100, buffer 5 -> stop 95.
    stop, capped = strategy._compute_long_stop(120.0, 100.0)
    assert stop == 95.0
    assert not capped


def test_short_stop_mirrors_long() -> None:
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())

    stop, capped = strategy._compute_short_stop(95.0, 100.0)  # dist 10 -> floor 15
    assert stop == 110.0
    assert not capped

    stop, capped = strategy._compute_short_stop(60.0, 100.0)  # dist 45 -> cap 31
    assert stop == 91.0
    assert capped

    stop, capped = strategy._compute_short_stop(80.0, 100.0)  # normal: 105
    assert stop == 105.0
    assert not capped


def _trade(exit_reason: str, entry_price: float = 100.0, exit_price: float = 99.0, stop_price: float = 80.0) -> Trade:
    return Trade(
        symbol="NQU2026",
        side="long",
        quantity=1,
        entry_timestamp_utc="2026-06-30T13:33:00Z",
        entry_price=entry_price,
        exit_timestamp_utc="2026-06-30T13:40:00Z",
        exit_price=exit_price,
        exit_reason=exit_reason,
        stop_price=stop_price,
        gross_points=exit_price - entry_price,
        gross_pnl=(exit_price - entry_price) * 2.0,
        commission=1.0,
        net_pnl=(exit_price - entry_price) * 2.0 - 1.0,
        mfe_points=1.0,
        mae_points=1.0,
        session_date="2026-06-30",
    )


def test_stop_exit_resets_stop_cooldown() -> None:
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    strategy._bars_since_stop_loss = 999

    strategy.on_trade_closed(_trade("stop"))
    strategy._advance_cooldowns()

    assert strategy._bars_since_stop_loss == 0
    assert strategy._position_side is None


def test_near_entry_exit_resets_breakeven_cooldown() -> None:
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    # Exit 1pt from entry with a 20pt locked stop distance -> breakeven-style.
    strategy.on_trade_closed(_trade("atf_flip", entry_price=100.0, exit_price=101.0, stop_price=80.0))
    strategy._advance_cooldowns()

    assert strategy._bars_since_breakeven_exit == 0
    assert strategy._bars_since_stop_loss > 0


def test_counters_freeze_while_position_open() -> None:
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    strategy._position_side = "long"
    before = strategy._bars_since_stop_loss

    strategy._advance_cooldowns()

    assert strategy._bars_since_stop_loss == before


def _synthetic_bars(days: int = 2) -> list[MarketBar]:
    """Deterministic two-session tape: drift + oscillation, RTH bars only."""
    bars = []
    price = 20000.0
    for day in range(days):
        base = datetime(2026, 6, 29 + day, 13, 30, tzinfo=timezone.utc)  # 9:30 ET
        for minute in range(390):  # 9:30 - 16:00 ET
            wobble = 6.0 * math.sin(minute / 9.0) + 2.5 * math.sin(minute / 2.3)
            drift = 0.15 * minute
            open_ = price
            close = 20000.0 + drift + wobble
            high = max(open_, close) + 1.5
            low = min(open_, close) - 1.5
            timestamp = (base + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")
            bars.append(
                MarketBar(
                    timestamp_utc=timestamp,
                    symbol="NQU2026",
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=100.0,
                )
            )
            price = close
    return bars


def test_full_simulation_smoke_respects_window_and_stop_bounds() -> None:
    bars = _synthetic_bars()
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    result = SimulationEngine(SimulationConfig()).run(bars, strategy)

    intents = [r for r in result.ledger.records if r.event_type == EventType.ORDER_INTENT]
    rejections = [r for r in result.ledger.records if r.event_type == EventType.REJECTION]

    # Rejections only carry known gate reasons.
    known = {
        "atf_warming_up", "ma_warming_up", "no_pivot", "below_trend_mas",
        "above_trend_mas", "sr_not_confirmed", "squeeze_momentum_not_green",
        "squeeze_momentum_not_red", "squeeze_not_released", "wings_fail", "cooldown",
    }
    assert rejections, "expected in-window rejected signals on a synthetic tape"
    assert {r.payload["reason"] for r in rejections} <= known

    # Any intent fired inside the 9:30-10:00 ET window with a stop within
    # the floor/cap band relative to the signal price.
    for intent in intents:
        minute_utc = int(intent.timestamp_utc[14:16])
        hour_utc = int(intent.timestamp_utc[11:13])
        et_minutes = (hour_utc - 4) * 60 + minute_utc
        assert 570 <= et_minutes < 600
        distance = abs(intent.payload["signal_price"] - intent.payload["stop_price"])
        assert 14.99 <= distance <= 31.01

    # Every closed trade has a legitimate exit reason.
    assert {t.exit_reason for t in result.trades} <= {
        "stop", "stop_gap", "atf_flip", "session_flatten", "session_end", "end_of_data",
    }


def test_finalize_prior_session_vol_matches_independently_computed_stdev() -> None:
    closes = [20000.0 + i * 0.37 for i in range(35)]
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    strategy._current_session_rth_closes = list(closes)

    strategy._finalize_prior_session_vol()

    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    expected = statistics.pstdev(returns)
    assert strategy._prior_session_realized_vol == pytest.approx(expected)
    assert strategy._current_session_rth_closes == []


def test_finalize_prior_session_vol_leaves_value_unchanged_when_insufficient_data() -> None:
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    strategy._prior_session_realized_vol = 0.0002  # a previously-computed value
    strategy._current_session_rth_closes = [20000.0 + i for i in range(10)]  # only 10 < 30

    strategy._finalize_prior_session_vol()

    assert strategy._prior_session_realized_vol == 0.0002
    assert strategy._current_session_rth_closes == []


def test_finalize_prior_session_vol_stays_none_on_cold_start() -> None:
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    strategy._current_session_rth_closes = [20000.0, 20001.0]

    strategy._finalize_prior_session_vol()

    assert strategy._prior_session_realized_vol is None


def _rth_bars_for_session(day: int, closes: list[float]) -> list[MarketBar]:
    """One RTH-only session's worth of 1-minute bars starting 9:30 ET."""
    base = datetime(2026, 6, 29 + day, 13, 30, tzinfo=timezone.utc)  # 9:30 ET
    bars = []
    prev_close = closes[0] - 1.0
    for minute, close in enumerate(closes):
        timestamp = (base + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bars.append(
            MarketBar(
                timestamp_utc=timestamp,
                symbol="NQU2026",
                open=prev_close,
                high=max(prev_close, close) + 0.5,
                low=min(prev_close, close) - 0.5,
                close=close,
                volume=100.0,
            )
        )
        prev_close = close
    return bars


def test_on_bar_accumulates_rth_closes_and_finalizes_vol_at_session_boundary() -> None:
    session1_closes = [20000.0 + i * 0.37 for i in range(35)]
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())

    for bar in _rth_bars_for_session(0, session1_closes):
        strategy.on_bar(bar)
    assert strategy._prior_session_realized_vol is None  # not finalized until session 2 starts
    assert len(strategy._current_session_rth_closes) == 35

    session2_bars = _rth_bars_for_session(1, [21000.0, 21001.0])
    strategy.on_bar(session2_bars[0])  # first bar of session 2 triggers the transition

    returns = [
        math.log(session1_closes[i] / session1_closes[i - 1]) for i in range(1, 35)
    ]
    expected = statistics.pstdev(returns)
    assert strategy._prior_session_realized_vol == pytest.approx(expected)
    assert strategy._current_session_rth_closes == [21000.0]


def test_prior_vol_gate_failing_blocks_when_enabled_and_above_threshold() -> None:
    config = AdaptiveTrendConfig(enable_prior_vol_gate=True, prior_vol_high_threshold=0.0005)
    strategy = AdaptiveTrendStrategy(config)
    strategy._prior_session_realized_vol = 0.0006

    assert strategy._prior_vol_gate_failing() == "prior_vol_gate"


def test_prior_vol_gate_failing_allows_when_enabled_and_below_threshold() -> None:
    config = AdaptiveTrendConfig(enable_prior_vol_gate=True, prior_vol_high_threshold=0.0005)
    strategy = AdaptiveTrendStrategy(config)
    strategy._prior_session_realized_vol = 0.0003

    assert strategy._prior_vol_gate_failing() is None


def test_prior_vol_gate_failing_allows_when_disabled_even_if_above_threshold() -> None:
    config = AdaptiveTrendConfig(enable_prior_vol_gate=False, prior_vol_high_threshold=0.0005)
    strategy = AdaptiveTrendStrategy(config)
    strategy._prior_session_realized_vol = 0.0006  # would trigger if enabled

    assert strategy._prior_vol_gate_failing() is None


def test_prior_vol_gate_failing_allows_on_cold_start_with_no_prior_vol_yet() -> None:
    config = AdaptiveTrendConfig(enable_prior_vol_gate=True, prior_vol_high_threshold=0.0005)
    strategy = AdaptiveTrendStrategy(config)
    # self._prior_session_realized_vol defaults to None (cold start)

    assert strategy._prior_vol_gate_failing() is None
