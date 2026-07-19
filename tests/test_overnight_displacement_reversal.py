from dataclasses import replace
from datetime import date, datetime, time, timezone
import math
from zoneinfo import ZoneInfo

import pytest

from full_python.data.databento import front_contract_for_session
from full_python.events import EventType
from full_python.models import MarketBar
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.overnight_displacement_reversal import (
    ODR_BRANCH,
    OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
    DisplacementRegime,
    DisplacementSide,
    DisplacementState,
    OvernightDisplacementFeatures,
    OvernightDisplacementReversalStrategy,
    classify_overnight_displacement_reversal,
)
from full_python.strategy.overnight_displacement_reversal_config import (
    OvernightDisplacementReversalConfig,
)


EASTERN = ZoneInfo("America/New_York")
CAUSAL_DTR_DATES = tuple(
    f"2026-05-{item:02d}"
    for item in (
        1, 4, 5, 6, 7, 8, 11, 12, 13, 14,
        15, 18, 19, 20, 21, 22, 25, 26, 27, 28,
    )
)


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


def _features(**overrides) -> OvernightDisplacementFeatures:
    values = dict(
        session_date="2026-07-09",
        classification_timestamp_utc="2026-07-09T13:30:00Z",
        setup_id="odr-v3:2026-07-09:short",
        current_rth_session=True,
        prior_rth_session_date="2026-07-08",
        prior_rth_contract="NQU6",
        current_contract="NQU6",
        prior_rth_complete=True,
        prior_rth_all_finite=True,
        prior_rth_expected_minutes=390,
        prior_rth_observed_minutes=390,
        roll_transition=False,
        complete_overnight=True,
        overnight_bar_count=930,
        overnight_first_offset_minutes=0,
        overnight_last_offset_minutes=929,
        overnight_max_gap_minutes=1,
        overnight_all_finite=True,
        overnight_total_volume=93000.0,
        overnight_high=125.0,
        overnight_low=99.0,
        overnight_close=120.0,
        overnight_range=26.0,
        overnight_vwap=112.0,
        prior_rth_close=100.0,
        dtr20=100.0,
        dtr_session_dates=CAUSAL_DTR_DATES,
        dtr_values=(100.0,) * 20,
        rth_open=105.0,
        gap_signed_points=5.0,
        gap_dtr=0.05,
        gap_direction="up",
        breadth_above_prior_close=0.50,
        breadth_below_prior_close=0.49,
        displacement_breadth=0.50,
    )
    values.update(overrides)
    return OvernightDisplacementFeatures(**values)


def _small_config(**overrides) -> OvernightDisplacementReversalConfig:
    values = dict(
        daily_range_lookback_sessions=2,
        overnight_max_gap_minutes=1000,
    )
    values.update(overrides)
    return OvernightDisplacementReversalConfig(**values)


def _ready_strategy(**overrides) -> OvernightDisplacementReversalStrategy:
    strategy = OvernightDisplacementReversalStrategy(_small_config(**overrides))
    strategy._dtr_history.extend((100.0, 100.0))
    strategy._dtr_session_dates.extend(("2026-07-06", "2026-07-07"))
    strategy._prior_rth_complete = True
    strategy._prior_rth_all_finite = True
    strategy._prior_rth_session_date = "2026-07-08"
    strategy._prior_rth_expected_minutes = 390
    strategy._prior_rth_observed_minutes = 390
    strategy._prior_rth_close = 100.0
    strategy._previous_rth_contract = "NQU6"
    return strategy


def _feed_overnight(
    strategy: OvernightDisplacementReversalStrategy,
    day: date,
    *,
    close: float,
) -> None:
    prior_calendar_day = day.fromordinal(day.toordinal() - 1)
    strategy.on_bar(
        _bar(prior_calendar_day, 18, 0, close, close + 1, close - 1, close)
    )
    strategy.on_bar(_bar(day, 9, 29, close, close + 1, close - 1, close))


def test_config_defaults_are_frozen_hashable_and_mode_free() -> None:
    config = OvernightDisplacementReversalConfig()
    assert config.min_gap_dtr == 0.05
    assert config.max_gap_dtr == 0.75
    assert config.min_displacement_breadth == 0.50
    assert config.extension_dtr == 0.02
    assert config.entry_end_minutes_et == 11 * 60
    assert config.hard_exit_fill_minutes_et == 12 * 60
    assert len(config.parameter_hash()) == 64
    assert not {"live", "backtest", "broker"} & config.to_dict().keys()


@pytest.mark.parametrize(
    "overrides, message",
    (
        ({"min_gap_dtr": 0.8}, "gap bounds"),
        ({"min_risk_dtr": 0.3}, "risk bounds"),
        ({"min_displacement_breadth": 1.1}, r"in \(0, 1\]"),
        ({"min_reward_r": 2.1}, "cannot exceed"),
        ({"entry_end_minutes_et": 9 * 60}, "entry window"),
    ),
)
def test_config_rejects_invalid_geometry(overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        OvernightDisplacementReversalConfig(**overrides)


def test_classifier_lower_gap_and_breadth_equalities_pass() -> None:
    classification = classify_overnight_displacement_reversal(
        _features(), OvernightDisplacementReversalConfig()
    )
    assert classification.regime == DisplacementRegime.ELIGIBLE_GAP
    assert classification.side == DisplacementSide.SHORT
    assert classification.setup_id == "odr-v3:2026-07-09:short"
    assert classification.gap_direction == "up"


def test_classifier_upper_gap_equality_and_exact_down_mirror_pass() -> None:
    config = OvernightDisplacementReversalConfig()
    upper = classify_overnight_displacement_reversal(
        _features(
            rth_open=175.0,
            gap_signed_points=75.0,
            gap_dtr=0.75,
        ),
        config,
    )
    assert upper.regime == DisplacementRegime.ELIGIBLE_GAP

    down = classify_overnight_displacement_reversal(
        _features(
            setup_id="odr-v3:2026-07-09:long",
            rth_open=95.0,
            gap_signed_points=-5.0,
            gap_direction="down",
            breadth_above_prior_close=0.49,
            breadth_below_prior_close=0.50,
            displacement_breadth=0.50,
        ),
        config,
    )
    assert down.regime == DisplacementRegime.ELIGIBLE_GAP
    assert down.side == DisplacementSide.LONG
    assert down.setup_id == "odr-v3:2026-07-09:long"


@pytest.mark.parametrize(
    "overrides, reason",
    (
        (
            {"prior_rth_complete": False, "prior_rth_observed_minutes": 389},
            "incomplete_prior_rth_session",
        ),
        (
            {"complete_overnight": False, "overnight_last_offset_minutes": 923},
            "incomplete_overnight_coverage",
        ),
        (
            {
                "session_date": "2026-06-15",
                "classification_timestamp_utc": "2026-06-15T13:30:00Z",
                "setup_id": "odr-v3:2026-06-15:short",
                "prior_rth_session_date": "2026-06-12",
                "prior_rth_contract": "NQM6",
                "current_contract": "NQU6",
                "roll_transition": True,
            },
            "continuous_contract_roll",
        ),
        (
            {"rth_open": 104.9, "gap_signed_points": 4.9, "gap_dtr": 0.049},
            "gap_below_minimum",
        ),
        (
            {"rth_open": 175.1, "gap_signed_points": 75.1, "gap_dtr": 0.751},
            "gap_above_maximum",
        ),
        (
            {
                "breadth_above_prior_close": 0.499,
                "displacement_breadth": 0.499,
            },
            "displacement_breadth_below_minimum",
        ),
        ({"dtr20": None}, "missing_or_nonfinite_reference_state"),
        ({"overnight_vwap": math.nan}, "missing_or_nonfinite_reference_state"),
    ),
)
def test_classifier_fails_closed_with_explicit_reason(overrides, reason) -> None:
    classification = classify_overnight_displacement_reversal(
        replace(_features(), **overrides), OvernightDisplacementReversalConfig()
    )
    assert classification.regime == DisplacementRegime.NO_TRADE
    assert classification.reason == reason


def test_zero_gap_has_no_direction_and_deterministic_none_setup_id() -> None:
    classification = classify_overnight_displacement_reversal(
        _features(
            rth_open=100.0,
            gap_signed_points=0.0,
            gap_dtr=0.0,
            gap_direction=None,
            displacement_breadth=None,
        ),
        OvernightDisplacementReversalConfig(),
    )
    assert classification.regime == DisplacementRegime.NO_TRADE
    assert classification.reason == "zero_gap_no_side"
    assert classification.side == DisplacementSide.NONE
    assert classification.setup_id == "odr-v3:2026-07-09:none"


@pytest.mark.parametrize(
    "overrides, reason",
    (
        ({"gap_signed_points": -5.0}, "inconsistent_gap_geometry"),
        ({"gap_dtr": 0.50}, "inconsistent_gap_geometry"),
        ({"gap_direction": "down"}, "inconsistent_gap_geometry"),
        ({"setup_id": "odr-v3:2026-07-09:long"}, "inconsistent_gap_geometry"),
        ({"displacement_breadth": 0.49}, "inconsistent_displacement_breadth"),
        (
            {"breadth_above_prior_close": 0.75, "breadth_below_prior_close": 0.75},
            "invalid_breadth_geometry",
        ),
    ),
)
def test_classifier_recomputes_and_rejects_forged_redundant_geometry(
    overrides, reason
) -> None:
    classification = classify_overnight_displacement_reversal(
        replace(_features(), **overrides), OvernightDisplacementReversalConfig()
    )
    assert classification.regime == DisplacementRegime.NO_TRADE
    assert classification.reason == reason


def test_classifier_rejects_stale_prior_session_and_forged_contract_codes() -> None:
    stale = classify_overnight_displacement_reversal(
        replace(_features(), prior_rth_session_date="2026-07-07"),
        OvernightDisplacementReversalConfig(),
    )
    assert stale.reason == "stale_prior_rth_session"

    forged = classify_overnight_displacement_reversal(
        replace(_features(), current_contract="NQZ6"),
        OvernightDisplacementReversalConfig(),
    )
    assert forged.reason == "inconsistent_contract_identity"


@pytest.mark.parametrize(
    "session_day, expected_close, expected_count",
    (
        (date(2026, 7, 9), 16 * 60, 390),
        (date(2026, 7, 3), 13 * 60, 210),
        (date(2025, 11, 28), 13 * 60 + 15, 225),
    ),
)
def test_complete_prior_rth_uses_calendar_specific_exact_final_minute(
    session_day, expected_close, expected_count
) -> None:
    strategy = OvernightDisplacementReversalStrategy(_small_config())
    strategy._session_date = session_day.isoformat()
    strategy._current_contract = front_contract_for_session(session_day)
    strategy._rth_minutes = list(range(9 * 60 + 30, expected_close))
    strategy._rth_minute_set = set(strategy._rth_minutes)
    strategy._rth_high = 120.0
    strategy._rth_low = 80.0
    strategy._rth_close = 110.0
    strategy._rth_all_finite = True

    strategy._finalize_session()

    assert strategy._prior_rth_complete
    assert strategy._prior_rth_expected_minutes == expected_count
    assert strategy._prior_rth_observed_minutes == expected_count
    assert strategy._prior_rth_close == 110.0

    strategy._rth_open = 120.0
    strategy._overnight_offsets = [0, 929]
    strategy._overnight_closes = [120.0, 120.0]
    strategy._overnight_total_volume = 200.0
    strategy._overnight_pv = 24000.0
    strategy._overnight_high = 121.0
    strategy._overnight_low = 119.0
    strategy._overnight_close = 120.0
    features = strategy._build_features(
        "2026-07-10T13:30:00Z", current_rth_session=True
    )
    assert features.prior_rth_session_date == session_day.isoformat()
    assert features.prior_rth_expected_minutes == expected_count
    assert features.prior_rth_observed_minutes == expected_count


def test_incomplete_prior_rth_clears_close_instead_of_reusing_older_reference() -> None:
    strategy = OvernightDisplacementReversalStrategy(_small_config())
    session_day = date(2026, 7, 9)
    strategy._session_date = session_day.isoformat()
    strategy._current_contract = front_contract_for_session(session_day)
    strategy._prior_rth_complete = True
    strategy._prior_rth_close = 100.0
    strategy._rth_minutes = list(range(9 * 60 + 30, 16 * 60 - 1))
    strategy._rth_high = 120.0
    strategy._rth_low = 80.0
    strategy._rth_close = 110.0

    strategy._finalize_session()

    assert not strategy._prior_rth_complete
    assert strategy._prior_rth_expected_minutes == 390
    assert strategy._prior_rth_observed_minutes == 389
    assert strategy._prior_rth_close is None


def test_calendar_full_closure_preserves_last_complete_rth_reference() -> None:
    strategy = OvernightDisplacementReversalStrategy(_small_config())
    strategy._session_date = "2025-12-25"
    strategy._current_contract = "NQZ5"
    strategy._previous_rth_contract = "NQZ5"
    strategy._prior_rth_session_date = "2025-12-24"
    strategy._prior_rth_complete = True
    strategy._prior_rth_expected_minutes = 225
    strategy._prior_rth_observed_minutes = 225
    strategy._prior_rth_close = 22100.0
    strategy._dtr_history.extend((300.0, 310.0))

    strategy._finalize_session()

    assert strategy._prior_rth_session_date == "2025-12-24"
    assert strategy._prior_rth_complete
    assert strategy._prior_rth_close == 22100.0
    assert strategy._previous_rth_contract == "NQZ5"
    assert list(strategy._dtr_history) == [300.0, 310.0]


def test_roll_session_is_no_trade_and_does_not_enter_dtr_but_seeds_new_close() -> None:
    strategy = OvernightDisplacementReversalStrategy(_small_config())
    session_day = date(2026, 6, 15)
    strategy._session_date = session_day.isoformat()
    strategy._current_contract = "NQU6"
    strategy._previous_rth_contract = "NQM6"
    strategy._roll_transition = True
    strategy._prior_rth_complete = True
    strategy._prior_rth_close = 100.0
    strategy._rth_minutes = list(range(9 * 60 + 30, 16 * 60))
    strategy._rth_minute_set = set(strategy._rth_minutes)
    strategy._rth_high = 120.0
    strategy._rth_low = 80.0
    strategy._rth_close = 110.0

    strategy._finalize_session()

    assert list(strategy._dtr_history) == []
    assert strategy._prior_rth_complete
    assert strategy._prior_rth_close == 110.0
    assert strategy._previous_rth_contract == "NQU6"

    classification = classify_overnight_displacement_reversal(
        replace(
            _features(),
            session_date="2026-06-15",
            classification_timestamp_utc="2026-06-15T13:30:00Z",
            setup_id="odr-v3:2026-06-15:short",
            prior_rth_session_date="2026-06-12",
            prior_rth_contract="NQM6",
            current_contract="NQU6",
            roll_transition=True,
        ),
        OvernightDisplacementReversalConfig(),
    )
    assert classification.reason == "continuous_contract_roll"


def test_overnight_window_excludes_1600_to_1759_and_checks_coverage_volume() -> None:
    strategy = _ready_strategy(overnight_max_gap_minutes=15)
    assert strategy._overnight_offset(16 * 60) is None
    assert strategy._overnight_offset(17 * 60 + 59) is None
    assert strategy._overnight_offset(18 * 60) == 0
    assert strategy._overnight_offset(9 * 60 + 29) == 929

    strategy._session_date = "2026-07-09"
    strategy._overnight_offsets = list(range(0, 930, 10)) + [929]
    strategy._overnight_closes = [120.0] * len(strategy._overnight_offsets)
    strategy._overnight_total_volume = float(len(strategy._overnight_offsets) * 100)
    strategy._overnight_pv = strategy._overnight_total_volume * 120.0
    strategy._overnight_high = 125.0
    strategy._overnight_low = 99.0
    strategy._overnight_close = 120.0
    strategy._rth_open = 120.0
    built = strategy._build_features("2026-07-09T13:30:00Z", current_rth_session=True)
    assert built.complete_overnight
    assert built.overnight_max_gap_minutes == 10
    assert built.displacement_breadth == 1.0

    strategy._overnight_total_volume = 0.0
    built = strategy._build_features("2026-07-09T13:30:00Z", current_rth_session=True)
    assert not built.complete_overnight


@pytest.mark.parametrize(
    "overrides, expected_regime, expected_reason",
    (
        (
            {
                "overnight_first_offset_minutes": 5,
                "overnight_last_offset_minutes": 924,
                "overnight_max_gap_minutes": 15,
            },
            DisplacementRegime.ELIGIBLE_GAP,
            "eligible_overnight_displacement",
        ),
        (
            {"complete_overnight": False, "overnight_first_offset_minutes": 6},
            DisplacementRegime.NO_TRADE,
            "incomplete_overnight_coverage",
        ),
        (
            {"complete_overnight": False, "overnight_last_offset_minutes": 923},
            DisplacementRegime.NO_TRADE,
            "incomplete_overnight_coverage",
        ),
        (
            {"complete_overnight": False, "overnight_max_gap_minutes": 16},
            DisplacementRegime.NO_TRADE,
            "incomplete_overnight_coverage",
        ),
        (
            {"complete_overnight": False, "overnight_first_offset_minutes": -1},
            DisplacementRegime.NO_TRADE,
            "incomplete_overnight_coverage",
        ),
        (
            {"complete_overnight": False, "overnight_last_offset_minutes": 930},
            DisplacementRegime.NO_TRADE,
            "incomplete_overnight_coverage",
        ),
        (
            {"complete_overnight": False, "overnight_max_gap_minutes": 0},
            DisplacementRegime.NO_TRADE,
            "incomplete_overnight_coverage",
        ),
    ),
)
def test_overnight_coverage_boundaries_are_inclusive_and_auditable(
    overrides, expected_regime, expected_reason
) -> None:
    classification = classify_overnight_displacement_reversal(
        replace(_features(), **overrides), OvernightDisplacementReversalConfig()
    )
    assert classification.regime == expected_regime
    assert classification.reason == expected_reason


@pytest.mark.parametrize(
    "overrides, reason",
    (
        ({"dtr_values": (100.0,) * 19}, "incomplete_dtr_provenance"),
        ({"dtr_values": (90.0,) * 20}, "inconsistent_dtr_provenance"),
        (
            {
                "dtr_session_dates": tuple(
                    list(CAUSAL_DTR_DATES[:-1]) + ["2026-07-09"]
                )
            },
            "inconsistent_dtr_provenance",
        ),
        (
            {
                "dtr_session_dates": tuple(
                    list(CAUSAL_DTR_DATES[:-1]) + ["2026-06-15"]
                )
            },
            "inconsistent_dtr_provenance",
        ),
    ),
)
def test_classifier_recomputes_dtr_median_and_date_provenance(overrides, reason) -> None:
    classification = classify_overnight_displacement_reversal(
        replace(_features(), **overrides), OvernightDisplacementReversalConfig()
    )
    assert classification.reason == reason


def test_roll_handover_snapshot_exposes_contracts_and_next_day_uses_new_close() -> None:
    strategy = OvernightDisplacementReversalStrategy(_small_config())
    strategy._dtr_history.extend((100.0, 100.0))
    strategy._dtr_session_dates.extend(("2026-06-11", "2026-06-12"))
    strategy._prior_rth_session_date = "2026-06-12"
    strategy._prior_rth_complete = True
    strategy._prior_rth_all_finite = True
    strategy._prior_rth_expected_minutes = 390
    strategy._prior_rth_observed_minutes = 390
    strategy._prior_rth_close = 100.0
    strategy._previous_rth_contract = "NQM6"
    roll_day = date(2026, 6, 15)
    strategy._start_session(roll_day)
    assert strategy._current_contract == "NQU6"
    assert strategy._roll_transition

    strategy._overnight_offsets = [0, 929]
    strategy._overnight_closes = [120.0, 120.0]
    strategy._overnight_total_volume = 200.0
    strategy._overnight_pv = 24000.0
    strategy._overnight_high = 121.0
    strategy._overnight_low = 119.0
    strategy._overnight_close = 120.0
    strategy._rth_open = 120.0
    classification = strategy._freeze_classification(
        "2026-06-15T13:30:00Z", current_rth_session=True
    )
    snapshot = strategy.session_diagnostics[-1]
    assert classification.reason == "continuous_contract_roll"
    assert snapshot.features.prior_rth_contract == "NQM6"
    assert snapshot.features.current_contract == "NQU6"

    strategy._rth_minutes = list(range(9 * 60 + 30, 16 * 60))
    strategy._rth_minute_set = set(strategy._rth_minutes)
    strategy._rth_high = 130.0
    strategy._rth_low = 110.0
    strategy._rth_close = 125.0
    history_before = tuple(strategy._dtr_history)
    strategy._start_session(date(2026, 6, 16))

    assert tuple(strategy._dtr_history) == history_before
    assert strategy._prior_rth_close == 125.0
    assert strategy._previous_rth_contract == "NQU6"
    assert strategy._current_contract == "NQU6"
    assert not strategy._roll_transition


def test_same_bar_extension_then_rejection_builds_short_structural_bracket() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_overnight(strategy, day, close=120.0)

    result = strategy.on_bar(_bar(day, 9, 30, 120.0, 123.0, 117.0, 118.0))

    assert len(result.order_intents) == 1
    intent = result.order_intents[0]
    assert intent.side == "sell"
    assert intent.reason == OVERNIGHT_DISPLACEMENT_REVERSAL_REASON
    assert intent.metadata["setup_id"] == "odr-v3:2026-07-09:short"
    assert intent.metadata["branch"] == ODR_BRANCH
    assert intent.metadata["gap_direction"] == "up"
    assert intent.metadata["stop_price"] == 125.0
    assert intent.metadata["target_price"] == 104.0
    assert intent.metadata["decision_risk_dtr"] == 0.07
    assert intent.metadata["extension_magnitude_dtr"] == 0.03
    assert intent.metadata["decisive_cross_distance_dtr"] == 0.02
    assert intent.metadata["close_location"] == pytest.approx(5 / 6)
    assert intent.metadata["structural_extreme"] == 123.0
    assert intent.metadata["target_distance_r"] == 2.0
    assert result.signal is not None
    assert result.signal.metadata == intent.metadata
    assert strategy._state == DisplacementState.ENTRY_PENDING
    assert [event.event for event in strategy.diagnostic_events[-3:]] == [
        "classified",
        "extension_armed",
        "entry_confirmed",
    ]


def test_down_gap_long_trigger_and_bracket_are_exact_mirror() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_overnight(strategy, day, close=80.0)

    result = strategy.on_bar(_bar(day, 9, 30, 80.0, 83.0, 77.0, 82.0))

    assert len(result.order_intents) == 1
    intent = result.order_intents[0]
    assert intent.side == "buy"
    assert intent.metadata["setup_id"] == "odr-v3:2026-07-09:long"
    assert intent.metadata["gap_direction"] == "down"
    assert intent.metadata["stop_price"] == 75.0
    assert intent.metadata["target_price"] == 96.0
    assert intent.metadata["decision_risk_dtr"] == 0.07


def test_first_decisive_cross_before_extension_is_terminal() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_overnight(strategy, day, close=120.0)
    first = strategy.on_bar(_bar(day, 9, 30, 120.0, 121.0, 117.0, 118.0))
    later = strategy.on_bar(_bar(day, 9, 31, 118.0, 124.0, 117.0, 118.0))

    assert not first.order_intents and not later.order_intents
    assert strategy._state == DisplacementState.DONE
    assert strategy.diagnostic_events[-1].metadata["reason"] == (
        "decisive_rejection_before_extension"
    )


def test_objective_touch_on_signal_bar_cancels_before_favorable_path_assumption() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_overnight(strategy, day, close=120.0)
    result = strategy.on_bar(_bar(day, 9, 30, 120.0, 123.0, 100.0, 118.0))

    assert not result.order_intents
    assert strategy._state == DisplacementState.DONE
    assert strategy.diagnostic_events[-1].metadata["reason"] == (
        "correction_objective_touched_before_entry"
    )


def test_failed_first_close_location_is_terminal_and_later_bar_cannot_rescue() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_overnight(strategy, day, close=120.0)
    first = strategy.on_bar(_bar(day, 9, 30, 120.0, 123.0, 110.0, 118.0))
    later = strategy.on_bar(_bar(day, 9, 31, 118.0, 124.0, 117.0, 118.0))

    assert not first.order_intents and not later.order_intents
    assert strategy.diagnostic_events[-1].metadata["reason"] == (
        "decisive_rejection_close_location_failed"
    )


def test_missing_active_rth_minute_cancels_instead_of_joining_distant_bars() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_overnight(strategy, day, close=120.0)
    strategy.on_bar(_bar(day, 9, 30, 120.0, 123.0, 119.0, 121.0))
    result = strategy.on_bar(_bar(day, 9, 32, 121.0, 123.0, 117.0, 118.0))

    assert not result.order_intents
    assert strategy._state == DisplacementState.DONE
    assert strategy.diagnostic_events[-1].metadata["reason"] == "active_rth_minute_gap"


def test_exact_1059_rejection_is_allowed_and_no_signal_expires_on_that_bar() -> None:
    day = date(2026, 7, 9)
    strategy = _ready_strategy()
    _feed_overnight(strategy, day, close=120.0)
    strategy.on_bar(_bar(day, 9, 30, 120.0, 123.0, 119.0, 121.0))
    for minute in range(31, 60):
        strategy.on_bar(_bar(day, 9, minute, 121.0, 122.0, 119.0, 121.0))
    for minute in range(0, 59):
        strategy.on_bar(_bar(day, 10, minute, 121.0, 122.0, 119.0, 121.0))
    signal = strategy.on_bar(_bar(day, 10, 59, 121.0, 122.0, 117.0, 118.0))
    assert len(signal.order_intents) == 1

    expired = _ready_strategy()
    _feed_overnight(expired, day, close=120.0)
    expired.on_bar(_bar(day, 9, 30, 120.0, 123.0, 119.0, 121.0))
    for minute in range(31, 60):
        expired.on_bar(_bar(day, 9, minute, 121.0, 122.0, 119.0, 121.0))
    for minute in range(0, 59):
        expired.on_bar(_bar(day, 10, minute, 121.0, 122.0, 119.0, 121.0))
    final = expired.on_bar(_bar(day, 10, 59, 121.0, 122.0, 119.0, 121.0))
    assert not final.order_intents
    assert expired._state == DisplacementState.DONE
    assert expired.diagnostic_events[-1].metadata["reason"] == "entry_window_expired"


def test_nonfinite_active_rth_bar_cancels_fail_closed() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_overnight(strategy, day, close=120.0)
    strategy.on_bar(_bar(day, 9, 30, 120.0, 123.0, 119.0, 121.0))
    result = strategy.on_bar(
        _bar(day, 9, 31, 121.0, math.nan, 117.0, 118.0)
    )
    assert not result.order_intents
    assert strategy.diagnostic_events[-1].metadata["reason"] == (
        "nonfinite_active_rth_bar"
    )


def test_risk_and_post_round_reward_geometry_are_rejected_explicitly() -> None:
    day = date(2026, 7, 9)
    too_wide = _ready_strategy(max_risk_dtr=0.06)
    _feed_overnight(too_wide, day, close=120.0)
    result = too_wide.on_bar(_bar(day, 9, 30, 120.0, 123.0, 117.0, 118.0))
    assert result.signal is not None
    assert result.signal.reason == "decision_risk_geometry"

    rounded = _ready_strategy(min_gap_dtr=0.01)
    rounded._prior_rth_close = 110.72
    _feed_overnight(rounded, day, close=120.0)
    result = rounded.on_bar(_bar(day, 9, 30, 120.0, 122.0, 116.0, 118.1))
    assert result.signal is not None
    assert result.signal.reason == "decision_reward_geometry"
    assert result.signal.metadata["target_distance_r"] < 1.25


def test_one_attempt_only_even_without_engine_fill_feedback() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    _feed_overnight(strategy, day, close=120.0)
    first = strategy.on_bar(_bar(day, 9, 30, 120.0, 123.0, 117.0, 118.0))
    second = strategy.on_bar(_bar(day, 9, 31, 118.0, 124.0, 117.0, 118.0))
    third = strategy.on_bar(_bar(day, 9, 32, 118.0, 124.0, 117.0, 118.0))

    assert len(first.order_intents) == 1
    assert not second.order_intents and not third.order_intents
    assert sum(
        event.event == "entry_confirmed" for event in strategy.diagnostic_events
    ) == 1


def test_simulator_fills_next_open_and_time_exits_at_1200() -> None:
    strategy = _ready_strategy()
    day = date(2026, 7, 9)
    prior_calendar_day = day.fromordinal(day.toordinal() - 1)
    bars = [
        _bar(prior_calendar_day, 18, 0, 120.0, 121.0, 119.0, 120.0),
        _bar(day, 9, 29, 120.0, 121.0, 119.0, 120.0),
        _bar(day, 9, 30, 120.0, 123.0, 117.0, 118.0),
        _bar(day, 9, 31, 118.0, 120.0, 115.0, 118.0),
        _bar(day, 11, 59, 118.0, 120.0, 115.0, 118.0),
        _bar(day, 12, 0, 118.0, 120.0, 115.0, 118.0),
    ]
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
        and record.payload.get("reason") == OVERNIGHT_DISPLACEMENT_REVERSAL_REASON
    ]
    assert len(entry_fills) == 1
    assert entry_fills[0].timestamp_utc == _timestamp(day, 9, 31)
    assert entry_fills[0].payload["price"] == 117.25
    assert len(result.trades) == 1
    assert result.trades[0].exit_timestamp_utc == _timestamp(day, 12, 0)
    assert result.trades[0].exit_reason == f"{ODR_BRANCH}_time_exit"
    events = [event.event for event in strategy.diagnostic_events]
    assert "filled" in events
    assert "time_exit_signalled" in events
    assert "trade_closed" in events


def test_time_exit_requires_exact_1159_signal_bar() -> None:
    day = date(2026, 7, 9)
    exact = _ready_strategy()
    exact._session_date = day.isoformat()
    exact._position_side = "short"
    exact_exit = exact._hard_exit(
        _bar(day, 11, 59, 118.0, 119.0, 117.0, 118.0), 11 * 60 + 59
    )
    assert len(exact_exit) == 1

    missing = _ready_strategy()
    missing._session_date = day.isoformat()
    missing._position_side = "short"
    delayed = missing._hard_exit(
        _bar(day, 12, 0, 118.0, 119.0, 117.0, 118.0), 12 * 60
    )
    assert delayed == ()
