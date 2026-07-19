from dataclasses import replace

import pytest

from full_python.events import EventLedger, EventType
from full_python.models import Trade
from full_python.research.opening_auction_retest import (
    attribute_retest_trades,
    build_opening_auction_retest_report,
)
from full_python.research.statistical_confidence import build_sharpe_confidence
from full_python.strategy.opening_auction_retest import (
    ACCEPTED_BREAK_REASON,
    RetestClassification,
    RetestDiagnosticEvent,
    RetestFeatures,
    RetestRegime,
    RetestSessionSnapshot,
    RetestSide,
    RetestState,
)


def _trade() -> Trade:
    return Trade(
        symbol="NQ1!",
        side="long",
        quantity=1,
        entry_timestamp_utc="2024-01-02T14:47:00Z",
        entry_price=110.75,
        exit_timestamp_utc="2024-01-02T16:30:00Z",
        exit_price=120.0,
        exit_reason="accepted_break_time_exit",
        stop_price=100.0,
        gross_points=9.25,
        gross_pnl=185.0,
        commission=10.0,
        net_pnl=175.0,
        mfe_points=10.0,
        mae_points=2.0,
        session_date="2024-01-02",
    )


def _ledger() -> EventLedger:
    ledger = EventLedger()
    ledger.append(
        EventType.BAR,
        timestamp_utc="2024-01-02T14:46:00Z",
        payload={"symbol": "NQ1!"},
    )
    ledger.append(
        EventType.SIGNAL_DECISION,
        timestamp_utc="2024-01-02T14:46:00Z",
        payload={
            "symbol": "NQ1!",
            "decision": "accepted",
            "side": "long",
            "reason": ACCEPTED_BREAK_REASON,
            "signal_price": 110.0,
            "stop_price": 100.0,
            "target_price": 135.0,
        },
    )
    ledger.append(
        EventType.ORDER_INTENT,
        timestamp_utc="2024-01-02T14:46:00Z",
        payload={
            "symbol": "NQ1!",
            "side": "buy",
            "quantity": 1,
            "order_type": "market_entry",
            "reason": ACCEPTED_BREAK_REASON,
            "signal_price": 110.0,
            "stop_price": 100.0,
            "target_price": 135.0,
            "reference_side": "high",
            "reference_type": "overnight_high",
            "reference_price": 105.0,
        },
    )
    ledger.append(
        EventType.BAR,
        timestamp_utc="2024-01-02T14:47:00Z",
        payload={"symbol": "NQ1!"},
    )
    ledger.append(
        EventType.FILL,
        timestamp_utc="2024-01-02T14:47:00Z",
        payload={
            "symbol": "NQ1!",
            "side": "buy",
            "quantity": 1,
            "price": 110.75,
            "reason": ACCEPTED_BREAK_REASON,
            "slippage_points": 0.75,
        },
    )
    trade = _trade()
    ledger.append(
        EventType.FILL,
        timestamp_utc=trade.exit_timestamp_utc,
        payload={
            "symbol": trade.symbol,
            "side": "sell",
            "quantity": trade.quantity,
            "price": trade.exit_price,
            "reason": trade.exit_reason,
            "slippage_points": 0.75,
        },
    )
    ledger.append(
        EventType.TRADE_CLOSED,
        timestamp_utc=trade.exit_timestamp_utc,
        payload=trade.to_payload(),
    )
    return ledger


def _features(session_date: str) -> RetestFeatures:
    return RetestFeatures(
        session_date=session_date,
        classification_timestamp_utc=f"{session_date}T14:44:00Z",
        complete_observation=True,
        roll_transition=False,
        complete_overnight=True,
        overnight_bar_count=900,
        overnight_max_gap_minutes=1,
        opening_minutes=tuple(range(570, 585)),
        opening_closes=(110.0,) * 15,
        dtr20=100.0,
        opening_volume_ratio=1.0,
        rth_open=100.0,
        opening_high=112.0,
        opening_low=99.0,
        opening_close=110.0,
        opening_width=13.0,
        opening_midpoint=105.5,
        displacement_dtr=0.10,
        efficiency_ratio=0.50,
        close_location=11 / 13,
        opening_vwap=108.0,
        closes_above_vwap=12,
        closes_below_vwap=3,
        overnight_high=105.0,
        overnight_low=90.0,
        prior_rth_high=104.0,
        prior_rth_low=91.0,
        prior_rth_close=100.0,
    )


def _snapshot(session_date: str) -> RetestSessionSnapshot:
    return RetestSessionSnapshot(
        _features(session_date),
        RetestClassification(
            RetestRegime.ACCEPTED_BREAK,
            RetestSide.LONG,
            "external_high_accepted",
            "high",
            "overnight_high",
            105.0,
        ),
    )


def test_trade_attribution_joins_frozen_intent_fill_and_trade() -> None:
    attributed = attribute_retest_trades([_trade()], _ledger())
    assert len(attributed) == 1
    assert attributed[0].entry.branch == "accepted_break"
    assert attributed[0].entry.reference_price == 105.0
    assert attributed[0].entry.signal_price == 110.0
    assert attributed[0].entry.target_price == 135.0


def test_report_keeps_zero_days_weeks_and_complete_half_years() -> None:
    snapshots = [_snapshot("2024-01-02"), _snapshot("2024-01-09")]
    events = [
        RetestDiagnosticEvent(
            "2024-01-02",
            "2024-01-02T14:44:00Z",
            "classified",
            "accepted_break",
            "long",
            RetestState.WAIT_FIRST_RETEST.value,
        ),
        RetestDiagnosticEvent(
            "2024-01-02",
            "2024-01-02T14:46:00Z",
            "entry_confirmed",
            "accepted_break",
            "long",
            RetestState.ENTRY_PENDING.value,
        ),
        RetestDiagnosticEvent(
            "2024-01-02",
            "2024-01-02T14:47:00Z",
            "filled",
            "accepted_break",
            "long",
            RetestState.POSITION.value,
        ),
        RetestDiagnosticEvent(
            "2024-01-09",
            "2024-01-09T14:44:00Z",
            "classified",
            "accepted_break",
            "long",
            RetestState.WAIT_FIRST_RETEST.value,
        ),
    ]
    report = build_opening_auction_retest_report(
        trades=[_trade()],
        ledger=_ledger(),
        snapshots=snapshots,
        diagnostic_events=events,
        point_value=20.0,
        score_start_session="2024-01-01",
        score_end_session_exclusive="2025-01-01",
        allocated_capital=100_000.0,
        hard_loss_limit=20_000.0,
    )

    realized_r = 175.0 / (10.75 * 20.0)
    score_sessions = report["score_window"]["gate_eligible_sessions"]
    assert score_sessions == 259
    assert report["daily"]["days_with_trades"] == 1
    assert report["weekly"]["week_count"] == 53
    assert report["weekly"]["realized_net_r"]["mean"] == pytest.approx(
        realized_r / report["weekly"]["week_count"]
    )
    assert report["weekly"]["weeks"][1]["net_pnl"] == 0.0
    assert list(report["complete_half_years"]) == ["2024-H1", "2024-H2"]
    assert report["complete_half_years"]["2024-H2"]["net_pnl_including_zero_days"] == 0
    assert report["risk_efficiency"]["observed_annualized_net_pnl"] == pytest.approx(
        175.0 * 252.0 / score_sessions
    )
    assert report["score_window"]["missing_expected_session_count"] == 257
    assert (
        report["t1_primary_gates"]["checks"]["zero_missing_expected_cme_sessions"]
        is False
    )
    assert report["allocated_capital_returns"]["status"] == "available"
    assert report["execution_diagnostics"]["reconciliation_violation_count"] == 0
    assert report["t1_primary_gates"]["passed"] is False
    assert report["promotion_status"] == "rejected_primary_no_threshold_rescue"


def test_report_excludes_warmup_snapshot_without_dropping_audit_count() -> None:
    ready = _snapshot("2024-01-03")
    warmup = RetestSessionSnapshot(
        replace(
            ready.features,
            session_date="2024-01-02",
            classification_timestamp_utc="2024-01-02T14:44:00Z",
            dtr20=None,
        ),
        RetestClassification(
            RetestRegime.NO_TRADE,
            RetestSide.NONE,
            "missing_reference_history",
        ),
    )
    report = build_opening_auction_retest_report(
        trades=[],
        ledger=EventLedger(),
        snapshots=[warmup, ready],
        diagnostic_events=[],
        point_value=20.0,
        score_start_session="2024-01-01",
        score_end_session_exclusive="2025-01-01",
    )
    assert report["score_window"]["classified_sessions_total_audit"] == 2
    assert report["score_window"]["gate_eligible_sessions"] == 258
    assert report["score_window"]["warmup_expected_sessions"] == 1
    assert report["score_window"]["missing_expected_session_count"] == 257


def test_sharpe_confidence_discloses_unknown_global_multiplicity() -> None:
    confidence = build_sharpe_confidence(
        [0.0, 1.0, -0.25, 0.75, 0.5] * 40,
        candidate_family_trial_budget=9,
    )
    assert confidence.annualized_sharpe is not None
    assert confidence.iid_psr_probability_sharpe_above_zero is not None
    assert confidence.dsr_probability is None
    assert confidence.dsr_status == "unavailable_insufficient_cross_trial_data"

    constant = build_sharpe_confidence(
        [1.0, 1.0, 1.0], candidate_family_trial_budget=9
    )
    assert constant.daily_sharpe is None
    assert constant.dsr_status == "unavailable_insufficient_cross_trial_data"

    cross_trial_confidence = build_sharpe_confidence(
        [0.0, 1.0, -0.25, 0.75, 0.5] * 40,
        candidate_family_trial_budget=9,
        related_trial_daily_sharpes=(0.05, 0.08, -0.02, 0.04, 0.10),
        effective_independent_trials=4,
    )
    assert cross_trial_confidence.dsr_benchmark_daily is not None
    assert cross_trial_confidence.dsr_probability is not None
    assert cross_trial_confidence.dsr_status == "available_cross_trial_dispersion"


def test_entry_invalidated_at_fill_is_an_explicit_reconciliation_failure() -> None:
    ledger = _ledger()
    ledger.records = ledger.records[:4]
    ledger.append(
        EventType.STATE_TRANSITION,
        timestamp_utc="2024-01-02T14:47:00Z",
        payload={
            "transition": "entry_invalidated_at_fill",
            "reason": "stop_not_protective_at_fill",
            "symbol": "NQ1!",
            "side": "long",
            "intent_timestamp_utc": "2024-01-02T14:46:00Z",
        },
    )
    events = [
        RetestDiagnosticEvent(
            "2024-01-02",
            "2024-01-02T14:46:00Z",
            "entry_confirmed",
            "accepted_break",
            "long",
            RetestState.ENTRY_PENDING.value,
        )
    ]
    report = build_opening_auction_retest_report(
        trades=[],
        ledger=ledger,
        snapshots=[_snapshot("2024-01-02")],
        diagnostic_events=events,
        point_value=20.0,
        score_start_session="2024-01-01",
        score_end_session_exclusive="2025-01-01",
    )
    assert report["execution_diagnostics"]["entry_invalidated_at_fill_count"] == 1
    assert "entry_invalidated_at_fill" in report["execution_diagnostics"][
        "reconciliation_violations"
    ]
    assert report["t1_primary_gates"]["checks"]["zero_reconciliation_violations"] is False
