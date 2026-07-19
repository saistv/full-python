from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from full_python.events import EventType
from full_python.models import Fill, MarketBar
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.opening_auction_regime import (
    AuctionRegime,
    AuctionSide,
    CONTINUATION_REASON,
    FAILED_AUCTION_REASON,
    OpeningAuctionFeatures,
    OpeningAuctionRegimeStrategy,
    classify_opening_auction,
)
from full_python.strategy.opening_auction_regime_config import (
    OpeningAuctionRegimeConfig,
)


EASTERN = ZoneInfo("America/New_York")


def _features(**overrides) -> OpeningAuctionFeatures:
    values = dict(
        session_date="2026-07-08",
        classification_timestamp_utc="2026-07-08T13:44:00Z",
        complete_observation=True,
        roll_transition=False,
        complete_overnight=True,
        overnight_bar_count=900,
        overnight_max_gap_minutes=1,
        opening_minutes=tuple(range(570, 585)),
        dtr20=100.0,
        opening_volume_ratio=1.1,
        rth_open=100.0,
        opening_high=121.0,
        opening_low=99.0,
        opening_close=120.0,
        opening_width=22.0,
        opening_midpoint=110.0,
        efficiency_ratio=0.8,
        close_location=21.0 / 22.0,
        opening_vwap=110.0,
        closes_above_vwap=13,
        closes_below_vwap=2,
        last_vwap_sides=("short", "short") + ("long",) * 13,
        overnight_high=110.0,
        overnight_low=90.0,
        prior_rth_high=115.0,
        prior_rth_low=85.0,
        prior_rth_close=100.0,
    )
    values.update(overrides)
    return OpeningAuctionFeatures(**values)


def test_config_defaults_are_frozen_and_hashable() -> None:
    config = OpeningAuctionRegimeConfig()
    assert config.observation_end_minutes_et == 9 * 60 + 45
    assert config.initiative_displacement_dtr == 0.15
    assert config.continuation_reward_r == 3.0
    assert config.failure_min_reward_r == 1.5
    assert len(config.parameter_hash()) == 64


def test_config_rejects_reversed_risk_bounds() -> None:
    with pytest.raises(ValueError, match="risk bounds"):
        OpeningAuctionRegimeConfig(
            continuation_min_risk_dtr=0.3,
            continuation_max_risk_dtr=0.2,
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "contracts",
        "observation_start_minutes_et",
        "observation_minutes",
        "continuation_entry_end_minutes_et",
        "hard_exit_fill_minutes_et",
        "daily_range_lookback_sessions",
        "opening_volume_lookback_sessions",
        "overnight_start_tolerance_minutes",
        "overnight_preopen_tolerance_minutes",
        "overnight_max_gap_minutes",
        "failure_vwap_reclaim_bars",
    ),
)
@pytest.mark.parametrize("invalid", (1.5, True))
def test_config_rejects_non_integer_count_and_time_fields(field_name, invalid) -> None:
    with pytest.raises(ValueError, match=f"{field_name} must be an integer"):
        OpeningAuctionRegimeConfig(**{field_name: invalid})


def test_classifier_finds_initiative_long_and_exact_short_mirror() -> None:
    config = OpeningAuctionRegimeConfig()
    long_result = classify_opening_auction(_features(), config)
    assert long_result.regime == AuctionRegime.INITIATIVE
    assert long_result.side == AuctionSide.LONG

    short = _features(
        opening_high=101.0,
        opening_low=79.0,
        opening_close=80.0,
        opening_midpoint=90.0,
        close_location=1.0 / 22.0,
        opening_vwap=90.0,
        closes_above_vwap=2,
        closes_below_vwap=13,
        last_vwap_sides=("long", "long") + ("short",) * 13,
        overnight_high=110.0,
        overnight_low=90.0,
        prior_rth_high=115.0,
        prior_rth_low=85.0,
    )
    short_result = classify_opening_auction(short, config)
    assert short_result.regime == AuctionRegime.INITIATIVE
    assert short_result.side == AuctionSide.SHORT


def test_classifier_finds_strong_failed_low_and_failed_high() -> None:
    config = OpeningAuctionRegimeConfig()
    failed_low = _features(
        rth_open=120.0,
        opening_high=120.0,
        opening_low=90.0,
        opening_close=110.0,
        opening_width=30.0,
        opening_midpoint=105.0,
        efficiency_ratio=0.2,
        close_location=2.0 / 3.0,
        closes_above_vwap=8,
        closes_below_vwap=7,
        last_vwap_sides=("short",) * 12 + ("long",) * 3,
        overnight_high=130.0,
        overnight_low=100.0,
        prior_rth_high=140.0,
        prior_rth_low=100.0,
        prior_rth_close=150.0,
    )
    result = classify_opening_auction(failed_low, config)
    assert result.regime == AuctionRegime.FAILED_AUCTION
    assert result.side == AuctionSide.LONG

    failed_high = _features(
        rth_open=80.0,
        opening_high=110.0,
        opening_low=80.0,
        opening_close=90.0,
        opening_width=30.0,
        opening_midpoint=95.0,
        efficiency_ratio=0.2,
        close_location=1.0 / 3.0,
        closes_above_vwap=7,
        closes_below_vwap=8,
        last_vwap_sides=("long",) * 12 + ("short",) * 3,
        overnight_high=100.0,
        overnight_low=70.0,
        prior_rth_high=100.0,
        prior_rth_low=60.0,
        prior_rth_close=50.0,
    )
    result = classify_opening_auction(failed_high, config)
    assert result.regime == AuctionRegime.FAILED_AUCTION
    assert result.side == AuctionSide.SHORT


def test_classifier_fails_closed_on_conflict_missing_data_and_roll() -> None:
    config = OpeningAuctionRegimeConfig()
    conflict = _features(
        opening_high=130.0,
        opening_low=80.0,
        opening_close=125.0,
        opening_width=50.0,
        opening_midpoint=105.0,
        close_location=0.9,
        last_vwap_sides=("short",) * 12 + ("long",) * 3,
        overnight_low=90.0,
        prior_rth_low=95.0,
    )
    assert classify_opening_auction(conflict, config).reason == "conflicting_auction_evidence"
    assert classify_opening_auction(
        replace(_features(), complete_observation=False), config
    ).reason == "incomplete_opening_observation"
    assert classify_opening_auction(
        replace(_features(), dtr20=None), config
    ).reason == "missing_reference_history"
    assert classify_opening_auction(
        replace(_features(), roll_transition=True), config
    ).reason == "continuous_contract_roll"
    assert classify_opening_auction(
        replace(_features(), complete_overnight=False), config
    ).reason == "incomplete_overnight_coverage"


def _timestamp(day: date, hour: int, minute: int) -> str:
    local = datetime.combine(day, time(hour, minute), tzinfo=EASTERN)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bar(day: date, hour: int, minute: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> MarketBar:
    return MarketBar(_timestamp(day, hour, minute), "NQ1!", o, h, l, c, v)


def _overnight_bars(session_day: date, high: float, low: float, close: float) -> list[MarketBar]:
    previous = session_day - timedelta(days=1)
    return [
        _bar(previous, 18, 0, close, high, low, close),
        _bar(session_day, 9, 29, close, close + 1.0, close - 1.0, close),
    ]


def _warmup_session(day: date) -> list[MarketBar]:
    bars = _overnight_bars(day, 160.0, 140.0, 150.0)
    for index in range(15):
        close = 150.0 + (0.25 if index % 2 == 0 else -0.25)
        bars.append(_bar(day, 9, 30 + index, 150.0, 151.0, 149.0, close))
    bars.append(_bar(day, 15, 59, 150.0, 200.0, 100.0, 150.0))
    return bars


def _small_config() -> OpeningAuctionRegimeConfig:
    return OpeningAuctionRegimeConfig(
        daily_range_lookback_sessions=2,
        opening_volume_lookback_sessions=2,
        overnight_max_gap_minutes=1000,
    )


def _warmed_strategy() -> OpeningAuctionRegimeStrategy:
    strategy = OpeningAuctionRegimeStrategy(_small_config())
    for bar in _warmup_session(date(2026, 7, 6)):
        strategy.on_bar(bar)
    for bar in _warmup_session(date(2026, 7, 7)):
        strategy.on_bar(bar)
    for bar in _warmup_session(date(2026, 7, 8)):
        strategy.on_bar(bar)
    return strategy


def _feed_initiative_opening(
    strategy: OpeningAuctionRegimeStrategy, day: date
):
    for bar in _overnight_bars(day, 110.0, 90.0, 100.0):
        strategy.on_bar(bar)
    opening = []
    previous = 100.0
    for index in range(15):
        close = 100.0 + 20.0 * (index + 1) / 15.0
        opening.append((previous, close + 1.0, previous - 1.0, close))
        previous = close
    result = None
    for index, values in enumerate(opening):
        result = strategy.on_bar(_bar(day, 9, 30 + index, *values))
    return result


def test_initiative_classification_freezes_at_last_observation_bar_then_arms_and_confirms() -> None:
    strategy = _warmed_strategy()
    day = date(2026, 7, 9)
    result = _feed_initiative_opening(strategy, day)
    assert not result.order_intents
    snapshot = strategy.session_diagnostics[-1]
    assert snapshot.features.opening_minutes == tuple(range(570, 585))
    assert snapshot.classification.regime == AuctionRegime.INITIATIVE
    assert snapshot.classification.side == AuctionSide.LONG

    armed = strategy.on_bar(_bar(day, 9, 45, 120.0, 120.0, 114.0, 118.0))
    assert not armed.order_intents
    confirmed = strategy.on_bar(_bar(day, 9, 46, 118.0, 122.0, 115.0, 121.0))
    assert len(confirmed.order_intents) == 1
    intent = confirmed.order_intents[0]
    assert intent.reason == "opening_auction_continuation"
    assert intent.metadata["stop_price"] < intent.metadata["signal_price"]
    assert intent.metadata["target_price"] > intent.metadata["signal_price"]
    assert intent.metadata["stop_price"] % 0.25 == 0

    frozen = snapshot.to_dict()
    strategy.on_bar(_bar(day, 9, 47, 121.0, 180.0, 80.0, 90.0))
    assert strategy.session_diagnostics[-1].to_dict() == frozen


def test_touching_opening_midpoint_cancels_continuation_permanently() -> None:
    strategy = _warmed_strategy()
    day = date(2026, 7, 9)
    _feed_initiative_opening(strategy, day)
    midpoint = strategy.session_diagnostics[-1].features.opening_midpoint
    assert midpoint is not None

    touched = strategy.on_bar(
        _bar(day, 9, 45, 120.0, 121.0, midpoint - 1.0, midpoint)
    )
    later = strategy.on_bar(_bar(day, 9, 46, midpoint, 140.0, midpoint, 139.0))

    assert not touched.order_intents
    assert not later.order_intents
    cancellations = [
        event
        for event in strategy.diagnostic_events
        if event.session_date == day.isoformat()
        and event.event == "continuation_cancelled"
    ]
    assert len(cancellations) == 1
    assert cancellations[0].metadata["reason"] == "midpoint_lost"


def _failed_low_target_session(day: date) -> list[MarketBar]:
    bars = [
        _bar(day, 9, 30, 120.0, 120.0, 90.0, 95.0),
        _bar(day, 9, 31, 95.0, 103.0, 94.0, 102.0),
        _bar(day, 9, 32, 102.0, 106.0, 101.0, 105.0),
        _bar(day, 9, 33, 105.0, 109.0, 104.0, 108.0),
        _bar(day, 9, 34, 108.0, 112.0, 107.0, 110.0),
    ]
    bars = _overnight_bars(day, 130.0, 100.0, 120.0) + bars
    for minute in range(35, 45):
        bars.append(_bar(day, 9, minute, 110.0, 112.0, 109.0, 110.0))
    bars.append(_bar(day, 9, 45, 110.0, 112.0, 109.0, 111.0))
    bars.append(_bar(day, 11, 29, 111.0, 113.0, 110.0, 112.0))
    bars.append(_bar(day, 11, 30, 112.0, 113.0, 111.0, 112.0))
    return bars


def test_failed_auction_decides_on_0934_and_fills_at_0945_next_open() -> None:
    bars = _warmup_session(date(2026, 7, 6))
    bars += _warmup_session(date(2026, 7, 7))
    bars += _warmup_session(date(2026, 7, 8))
    bars += _failed_low_target_session(date(2026, 7, 9))
    strategy = OpeningAuctionRegimeStrategy(_small_config())
    result = SimulationEngine(
        SimulationConfig(
            point_value=20.0,
            commission_per_contract_round_trip=10.0,
            entry_slippage_points=0.75,
            exit_slippage_points=0.75,
            rth_open_extra_entry_slippage_points=0.0,
        )
    ).run(bars, strategy)
    fills = [
        record
        for record in result.ledger.records
        if record.event_type == EventType.FILL
        and record.payload.get("reason") == FAILED_AUCTION_REASON
    ]
    assert len(fills) == 1
    assert fills[0].timestamp_utc == _timestamp(date(2026, 7, 9), 9, 45)
    assert fills[0].payload["side"] == "buy"
    signals = [
        record
        for record in result.ledger.records
        if record.event_type == EventType.SIGNAL_DECISION
        and record.payload.get("reason") == FAILED_AUCTION_REASON
    ]
    assert signals[0].timestamp_utc == _timestamp(date(2026, 7, 9), 9, 44)
    assert len(result.trades) == 1
    assert result.trades[0].exit_timestamp_utc == _timestamp(date(2026, 7, 9), 11, 30)
    assert result.trades[0].exit_reason == "failed_auction_time_exit"
    current_events = [
        event.event
        for event in strategy.diagnostic_events
        if event.session_date == "2026-07-09"
    ]
    assert current_events == [
        "classified",
        "entry_confirmed",
        "filled",
        "time_exit_signalled",
        "trade_closed",
    ]


def test_stale_open_position_blocks_new_entry_confirmation() -> None:
    strategy = _warmed_strategy()
    strategy.on_fill(
        Fill(
            timestamp_utc="2026-07-08T15:00:00Z",
            symbol="NQ1!",
            side="buy",
            quantity=1,
            price=150.0,
            reason=CONTINUATION_REASON,
        )
    )
    day = date(2026, 7, 9)
    results = [strategy.on_bar(bar) for bar in _failed_low_target_session(day)[:17]]

    assert strategy.session_diagnostics[-1].classification.regime == AuctionRegime.FAILED_AUCTION
    assert all(not result.order_intents for result in results)
    assert not any(
        event.session_date == day.isoformat() and event.event == "entry_confirmed"
        for event in strategy.diagnostic_events
    )


def test_cli_build_strategy_registers_opening_auction_regime() -> None:
    from full_python.cli import build_strategy

    config, strategy = build_strategy("opening_auction_regime")
    assert isinstance(config, OpeningAuctionRegimeConfig)
    assert isinstance(strategy, OpeningAuctionRegimeStrategy)
