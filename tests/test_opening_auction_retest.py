from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from full_python.events import EventType
from full_python.models import MarketBar
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.opening_auction_retest import (
    ACCEPTED_BREAK_REASON,
    OpeningAuctionRetestStrategy,
    RetestClassification,
    RetestFeatures,
    RetestRegime,
    RetestSide,
    RetestState,
    classify_opening_auction_retest,
)
from full_python.strategy.opening_auction_retest_config import (
    OpeningAuctionRetestConfig,
)


EASTERN = ZoneInfo("America/New_York")


def _features(**overrides) -> RetestFeatures:
    values = dict(
        session_date="2024-06-03",
        classification_timestamp_utc="2024-06-03T13:44:00Z",
        complete_observation=True,
        roll_transition=False,
        complete_overnight=True,
        overnight_bar_count=900,
        overnight_max_gap_minutes=1,
        opening_minutes=tuple(range(570, 585)),
        opening_closes=(108.0,) * 10 + (112.0,) * 5,
        dtr20=100.0,
        opening_volume_ratio=1.0,
        rth_open=100.0,
        opening_high=113.0,
        opening_low=95.0,
        opening_close=112.0,
        opening_width=18.0,
        opening_midpoint=104.0,
        displacement_dtr=0.12,
        efficiency_ratio=0.5,
        close_location=17.0 / 18.0,
        opening_vwap=111.0,
        closes_above_vwap=10,
        closes_below_vwap=5,
        overnight_high=110.0,
        overnight_low=90.0,
        prior_rth_high=108.0,
        prior_rth_low=92.0,
        prior_rth_close=100.0,
    )
    values.update(overrides)
    return RetestFeatures(**values)


def test_config_defaults_are_frozen_hashable_and_mode_free() -> None:
    config = OpeningAuctionRetestConfig()
    assert config.observation_end_minutes_et == 9 * 60 + 45
    assert config.entry_end_minutes_et == 10 * 60 + 30
    assert config.acceptance_closes_required == 4
    assert config.confirmation_max_bars == 3
    assert config.reward_r == 2.5
    assert len(config.parameter_hash()) == 64
    assert "live" not in config.to_dict()
    assert "backtest" not in config.to_dict()
    assert "broker" not in config.to_dict()


@pytest.mark.parametrize(
    "overrides, message",
    (
        ({"acceptance_closes_required": 6}, "exceeds"),
        ({"confirmation_max_bars": 0}, "positive"),
        ({"min_risk_dtr": 0.2, "max_risk_dtr": 0.1}, "risk bounds"),
        ({"retest_bar_close_location_min": 1.1}, r"in \(0, 1\]"),
    ),
)
def test_config_rejects_invalid_geometry(overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        OpeningAuctionRetestConfig(**overrides)


def test_classifier_finds_accepted_and_rejected_high_breaks() -> None:
    config = OpeningAuctionRetestConfig()
    accepted = classify_opening_auction_retest(_features(), config)
    assert accepted.regime == RetestRegime.ACCEPTED_BREAK
    assert accepted.side == RetestSide.LONG
    assert accepted.reference_type == "overnight_high"
    assert accepted.reference_price == 110.0

    rejected = classify_opening_auction_retest(
        _features(
            opening_closes=(112.0,) * 12 + (108.0,) * 3,
            opening_close=108.0,
            opening_vwap=109.0,
        ),
        config,
    )
    assert rejected.regime == RetestRegime.REJECTED_BREAK
    assert rejected.side == RetestSide.SHORT
    assert rejected.reference_side == "high"


def test_classifier_is_an_exact_low_side_mirror() -> None:
    config = OpeningAuctionRetestConfig()
    accepted = classify_opening_auction_retest(
        _features(
            opening_high=105.0,
            opening_low=87.0,
            opening_close=88.0,
            opening_closes=(92.0,) * 10 + (88.0,) * 5,
            opening_vwap=89.0,
            close_location=1 / 18,
        ),
        config,
    )
    assert accepted.regime == RetestRegime.ACCEPTED_BREAK
    assert accepted.side == RetestSide.SHORT
    assert accepted.reference_type == "overnight_low"

    rejected = classify_opening_auction_retest(
        _features(
            opening_high=105.0,
            opening_low=87.0,
            opening_close=92.0,
            opening_closes=(88.0,) * 12 + (92.0,) * 3,
            opening_vwap=91.0,
        ),
        config,
    )
    assert rejected.regime == RetestRegime.REJECTED_BREAK
    assert rejected.side == RetestSide.LONG


def test_classifier_fails_closed_on_conflict_missing_data_and_roll() -> None:
    config = OpeningAuctionRetestConfig()
    conflict = _features(
        opening_low=87.0,
        opening_close=88.0,
        opening_closes=(88.0,) * 15,
        opening_vwap=88.0,
    )
    assert (
        classify_opening_auction_retest(conflict, config).reason
        == "conflicting_auction_evidence"
    )
    assert classify_opening_auction_retest(
        replace(_features(), complete_observation=False), config
    ).reason == "incomplete_opening_observation"
    assert classify_opening_auction_retest(
        replace(_features(), complete_overnight=False), config
    ).reason == "incomplete_overnight_coverage"
    assert classify_opening_auction_retest(
        replace(_features(), roll_transition=True), config
    ).reason == "continuous_contract_roll"
    assert classify_opening_auction_retest(
        replace(_features(), dtr20=None), config
    ).reason == "missing_reference_history"
    assert classify_opening_auction_retest(
        replace(
            _features(),
            opening_closes=(108.0,) * 14 + (float("nan"),),
        ),
        config,
    ).reason == "nonfinite_opening_closes"


def test_equal_overnight_and_prior_reference_has_an_explicit_audit_label() -> None:
    classification = classify_opening_auction_retest(
        _features(prior_rth_high=110.0), OpeningAuctionRetestConfig()
    )
    assert classification.regime == RetestRegime.ACCEPTED_BREAK
    assert classification.reference_price == 110.0
    assert classification.reference_type == "overnight_and_prior_rth_high"


def _timestamp(day: date, hour: int, minute: int) -> str:
    local = datetime.combine(day, time(hour, minute), tzinfo=EASTERN)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bar(
    day: date,
    hour: int,
    minute: int,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float = 100.0,
) -> MarketBar:
    return MarketBar(_timestamp(day, hour, minute), "NQ1!", o, h, l, c, v)


def _small_config(**overrides) -> OpeningAuctionRetestConfig:
    values = dict(
        daily_range_lookback_sessions=2,
        opening_volume_lookback_sessions=2,
        overnight_max_gap_minutes=1000,
    )
    values.update(overrides)
    return OpeningAuctionRetestConfig(**values)


def _ready_strategy(**config_overrides) -> OpeningAuctionRetestStrategy:
    strategy = OpeningAuctionRetestStrategy(_small_config(**config_overrides))
    strategy._dtr_history.extend((100.0, 100.0))
    strategy._opening_volume_history.extend((1500.0, 1500.0))
    strategy._prior_rth_high = 108.0
    strategy._prior_rth_low = 92.0
    strategy._prior_rth_close = 100.0
    return strategy


def _feed_accepted_high_opening(
    strategy: OpeningAuctionRetestStrategy, day: date
) -> None:
    previous = day - timedelta(days=1)
    strategy.on_bar(_bar(previous, 18, 0, 100.0, 110.0, 90.0, 100.0))
    strategy.on_bar(_bar(day, 9, 29, 100.0, 101.0, 99.0, 100.0))
    strategy.on_bar(_bar(day, 9, 30, 100.0, 113.0, 99.0, 112.0))
    for minute in range(31, 45):
        strategy.on_bar(_bar(day, 9, minute, 112.0, 113.0, 111.0, 112.0))


def _feed_accepted_low_opening(
    strategy: OpeningAuctionRetestStrategy, day: date
) -> None:
    previous = day - timedelta(days=1)
    strategy.on_bar(_bar(previous, 18, 0, 100.0, 110.0, 90.0, 100.0))
    strategy.on_bar(_bar(day, 9, 29, 100.0, 101.0, 99.0, 100.0))
    strategy.on_bar(_bar(day, 9, 30, 100.0, 101.0, 87.0, 88.0))
    for minute in range(31, 45):
        strategy.on_bar(_bar(day, 9, minute, 88.0, 89.0, 87.0, 88.0))


def test_first_retest_then_later_confirmation_builds_fresh_structural_bracket() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_accepted_high_opening(strategy, day)
    snapshot = strategy.session_diagnostics[-1]
    assert snapshot.classification.regime == RetestRegime.ACCEPTED_BREAK
    assert snapshot.classification.reference_price == 110.0

    armed = strategy.on_bar(_bar(day, 9, 45, 112.0, 114.0, 109.5, 113.5))
    assert not armed.order_intents
    assert strategy._entry_state == RetestState.ARMED

    confirmed = strategy.on_bar(_bar(day, 9, 46, 113.5, 115.0, 111.0, 114.5))
    assert len(confirmed.order_intents) == 1
    intent = confirmed.order_intents[0]
    assert intent.reason == ACCEPTED_BREAK_REASON
    assert intent.metadata["stop_price"] == 107.5
    assert intent.metadata["target_price"] == 132.0
    assert intent.metadata["decision_risk_dtr"] == 0.07
    assert strategy._entry_state == RetestState.ENTRY_PENDING


def test_short_side_retest_confirmation_and_bracket_are_exact_mirrors() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_accepted_low_opening(strategy, day)
    snapshot = strategy.session_diagnostics[-1]
    assert snapshot.classification.regime == RetestRegime.ACCEPTED_BREAK
    assert snapshot.classification.side == RetestSide.SHORT
    assert snapshot.classification.reference_price == 90.0

    armed = strategy.on_bar(_bar(day, 9, 45, 88.0, 90.5, 86.0, 87.0))
    assert not armed.order_intents
    assert strategy._entry_state == RetestState.ARMED

    confirmed = strategy.on_bar(_bar(day, 9, 46, 87.0, 89.0, 84.0, 85.5))
    assert len(confirmed.order_intents) == 1
    intent = confirmed.order_intents[0]
    assert intent.side == "sell"
    assert intent.reason == ACCEPTED_BREAK_REASON
    assert intent.metadata["stop_price"] == 92.5
    assert intent.metadata["target_price"] == 68.0
    assert intent.metadata["decision_risk_dtr"] == 0.07


def test_first_contact_is_decisive_and_never_selects_a_later_prettier_retest() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_accepted_high_opening(strategy, day)

    strategy.on_bar(_bar(day, 9, 45, 112.0, 113.0, 108.0, 109.0))
    later = strategy.on_bar(_bar(day, 9, 46, 112.0, 114.0, 109.5, 113.5))

    assert strategy._entry_state == RetestState.DONE
    assert not later.order_intents
    cancellations = [
        event
        for event in strategy.diagnostic_events
        if event.event == "entry_cancelled"
    ]
    assert cancellations[-1].metadata["reason"] == "first_retest_failed_hold"


def test_confirmation_expires_after_three_bars_and_reference_invalidation_cancels() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_accepted_high_opening(strategy, day)
    strategy.on_bar(_bar(day, 9, 45, 112.0, 114.0, 109.5, 113.5))
    for minute in (46, 47, 48):
        strategy.on_bar(_bar(day, 9, minute, 113.0, 114.0, 111.0, 113.0))
    assert strategy._entry_state == RetestState.DONE
    assert strategy.diagnostic_events[-1].metadata["reason"] == "confirmation_expired"

    strategy = _ready_strategy()
    _feed_accepted_high_opening(strategy, day)
    strategy.on_bar(_bar(day, 9, 45, 112.0, 114.0, 109.5, 113.5))
    strategy.on_bar(_bar(day, 9, 46, 113.0, 114.0, 108.0, 108.5))
    assert strategy._entry_state == RetestState.DONE
    assert strategy.diagnostic_events[-1].metadata["reason"] == "armed_reference_invalidated"


def test_risk_geometry_rejects_and_late_first_retest_cannot_arm() -> None:
    strategy = _ready_strategy(max_risk_dtr=0.05)
    day = date(2026, 7, 9)
    _feed_accepted_high_opening(strategy, day)
    strategy.on_bar(_bar(day, 9, 45, 112.0, 114.0, 109.5, 113.5))
    rejected = strategy.on_bar(_bar(day, 9, 46, 113.5, 115.0, 111.0, 114.5))
    assert rejected.signal is not None
    assert rejected.signal.decision == "rejected"
    assert rejected.signal.reason == "retest_risk_geometry"

    strategy = _ready_strategy()
    _feed_accepted_high_opening(strategy, day)
    for minute in range(45, 59):
        strategy.on_bar(_bar(day, 9, minute, 115.0, 116.0, 114.0, 115.0))
    for minute in range(0, 29):
        strategy.on_bar(_bar(day, 10, minute, 115.0, 116.0, 114.0, 115.0))
    strategy.on_bar(_bar(day, 10, 29, 112.0, 114.0, 109.5, 113.5))
    assert strategy._entry_state == RetestState.DONE
    assert strategy.diagnostic_events[-1].metadata["reason"] == "first_retest_window_expired"


def test_confirmation_at_1029_is_allowed_but_a_first_retest_at_1029_is_not() -> None:
    day = date(2026, 7, 9)
    strategy = _ready_strategy()
    _feed_accepted_high_opening(strategy, day)
    for minute in range(45, 60):
        strategy.on_bar(_bar(day, 9, minute, 115.0, 116.0, 114.0, 115.0))
    for minute in range(0, 28):
        strategy.on_bar(_bar(day, 10, minute, 115.0, 116.0, 114.0, 115.0))
    strategy.on_bar(_bar(day, 10, 28, 112.0, 114.0, 109.5, 113.5))
    confirmed = strategy.on_bar(_bar(day, 10, 29, 113.5, 115.0, 111.0, 114.5))
    assert len(confirmed.order_intents) == 1
    assert strategy._entry_state == RetestState.ENTRY_PENDING

    strategy = _ready_strategy()
    _feed_accepted_high_opening(strategy, day)
    for minute in range(45, 60):
        strategy.on_bar(_bar(day, 9, minute, 115.0, 116.0, 114.0, 115.0))
    for minute in range(0, 29):
        strategy.on_bar(_bar(day, 10, minute, 115.0, 116.0, 114.0, 115.0))
    late = strategy.on_bar(_bar(day, 10, 29, 112.0, 114.0, 109.5, 113.5))
    assert not late.order_intents
    assert strategy._entry_state == RetestState.DONE
    assert strategy.diagnostic_events[-1].metadata["reason"] == "first_retest_window_expired"


def test_roll_session_does_not_enter_causal_dtr_history() -> None:
    strategy = OpeningAuctionRetestStrategy(_small_config())
    strategy._session_date = "2026-06-15"
    strategy._classification = RetestClassification(
        RetestRegime.NO_TRADE, RetestSide.NONE, "continuous_contract_roll"
    )
    strategy._rth_started = True
    strategy._roll_transition = True
    strategy._current_contract = "NQU6"
    strategy._prior_rth_close = 100.0
    strategy._rth_high = 120.0
    strategy._rth_low = 80.0
    strategy._rth_close = 110.0

    strategy._finalize_session()

    assert list(strategy._dtr_history) == []
    assert strategy._prior_rth_high == 120.0
    assert strategy._prior_rth_low == 80.0
    assert strategy._prior_rth_close == 110.0
    assert strategy._previous_rth_contract == "NQU6"


def test_simulator_fills_next_open_and_time_exits_at_1130() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    bars = []
    previous = day - timedelta(days=1)
    bars.append(_bar(previous, 18, 0, 100.0, 110.0, 90.0, 100.0))
    bars.append(_bar(day, 9, 29, 100.0, 101.0, 99.0, 100.0))
    bars.append(_bar(day, 9, 30, 100.0, 113.0, 99.0, 112.0))
    for minute in range(31, 45):
        bars.append(_bar(day, 9, minute, 112.0, 113.0, 111.0, 112.0))
    bars.extend(
        (
            _bar(day, 9, 45, 112.0, 114.0, 109.5, 113.5),
            _bar(day, 9, 46, 113.5, 115.0, 111.0, 114.5),
            _bar(day, 9, 47, 115.0, 116.0, 114.0, 115.0),
            _bar(day, 11, 29, 116.0, 117.0, 115.0, 116.0),
            _bar(day, 11, 30, 116.0, 117.0, 115.0, 116.0),
        )
    )
    result = SimulationEngine(
        SimulationConfig(
            point_value=20.0,
            commission_per_contract_round_trip=10.0,
            entry_slippage_points=0.75,
            exit_slippage_points=0.75,
            rth_open_extra_entry_slippage_points=0.0,
        )
    ).run(bars, strategy)

    entry_fills = [
        record
        for record in result.ledger.records
        if record.event_type == EventType.FILL
        and record.payload.get("reason") == ACCEPTED_BREAK_REASON
    ]
    assert len(entry_fills) == 1
    assert entry_fills[0].timestamp_utc == _timestamp(day, 9, 47)
    assert entry_fills[0].payload["price"] == 115.75
    assert len(result.trades) == 1
    assert result.trades[0].exit_timestamp_utc == _timestamp(day, 11, 30)
    assert result.trades[0].exit_reason == "accepted_break_time_exit"
