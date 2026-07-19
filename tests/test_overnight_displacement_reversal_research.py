from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from full_python.events import EventLedger, EventRecord, EventType
from full_python.data.databento import front_contract_for_session
from full_python.data.exchange_calendar import rth_close_minutes_et
from full_python.models import Trade
from full_python.research.overnight_displacement_reversal import (
    OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
    attribute_overnight_displacement_trades,
    audit_overnight_displacement_reconciliation,
    build_overnight_displacement_reversal_report,
    evaluate_odr_t1_primary_gates,
)


SESSION = "2024-01-02"
SETUP_ID = f"odr-v3:{SESSION}:short"
SIGNAL_TIME = "2024-01-02T14:35:00Z"
ENTRY_TIME = "2024-01-02T14:36:00Z"
EXIT_SIGNAL_TIME = "2024-01-02T16:59:00Z"
EXIT_TIME = "2024-01-02T17:00:00Z"


def _history_days() -> tuple[date, ...]:
    cursor = date(2024, 1, 2) - timedelta(days=1)
    result: list[date] = []
    while len(result) < 35:
        if rth_close_minutes_et(cursor) is not None:
            result.append(cursor)
        cursor -= timedelta(days=1)
    return tuple(sorted(result))


HISTORY_DAYS = _history_days()
HISTORY_DAY_SET = set(HISTORY_DAYS)
DTR_SOURCE_DAYS = tuple(
    day
    for day in HISTORY_DAYS
    if (day - timedelta(days=1) in HISTORY_DAY_SET or any(prior < day for prior in HISTORY_DAYS))
    and (
        (lambda prior: prior is not None and prior in HISTORY_DAY_SET and front_contract_for_session(day) == front_contract_for_session(prior))(
            next((prior for prior in reversed(HISTORY_DAYS) if prior < day), None)
        )
    )
)[-20:]


@dataclass(frozen=True)
class SyntheticFeatures:
    session_date: str
    classification_timestamp_utc: str
    setup_id: str
    current_rth_session: bool = True
    prior_rth_session_date: str = "2023-12-29"
    prior_rth_contract: str = "NQH4"
    current_contract: str = "NQH4"
    prior_rth_complete: bool = True
    prior_rth_all_finite: bool = True
    prior_rth_expected_minutes: int = 390
    prior_rth_observed_minutes: int = 390
    roll_transition: bool = False
    complete_overnight: bool = True
    overnight_bar_count: int = 900
    overnight_first_offset_minutes: int = 0
    overnight_last_offset_minutes: int = 929
    overnight_max_gap_minutes: int = 2
    overnight_all_finite: bool = True
    overnight_total_volume: float = 900.0
    overnight_high: float = 101.0
    overnight_low: float = 89.0
    overnight_close: float = 90.0
    overnight_range: float = 12.0
    overnight_vwap: float = 97.5
    prior_rth_close: float = 95.0
    dtr20: float | None = 100.0
    dtr_session_dates: tuple[str, ...] = tuple(day.isoformat() for day in DTR_SOURCE_DAYS)
    dtr_values: tuple[float, ...] = (100.0,) * 20
    rth_open: float = 110.0
    gap_signed_points: float = 15.0
    gap_dtr: float = 0.15
    gap_direction: str = "up"
    breadth_above_prior_close: float = 0.75
    breadth_below_prior_close: float = 0.25
    displacement_breadth: float = 0.75


@dataclass(frozen=True)
class SyntheticClassification:
    regime: str
    side: str
    reason: str
    setup_id: str
    gap_direction: str


@dataclass(frozen=True)
class SyntheticSnapshot:
    features: SyntheticFeatures
    classification: SyntheticClassification


@dataclass(frozen=True)
class SyntheticDiagnostic:
    session_date: str
    timestamp_utc: str
    event: str
    setup_id: str | None
    branch: str = OVERNIGHT_DISPLACEMENT_REVERSAL_REASON
    gap_direction: str = "up"
    side: str = "short"
    state: str = "entry_pending"
    metadata: dict[str, Any] | None = None


def _snapshot(
    session: str = SESSION,
    *,
    dtr20: float | None = 100.0,
    eligible: bool = True,
) -> SyntheticSnapshot:
    side = "short" if eligible else "none"
    return SyntheticSnapshot(
        SyntheticFeatures(
            session_date=session,
            classification_timestamp_utc=f"{session}T14:30:00Z",
            setup_id=f"odr-v3:{session}:{side}",
            dtr20=dtr20,
        ),
        SyntheticClassification(
            regime="eligible_gap" if eligible else "no_trade",
            side=side,
            reason="eligible_gap" if eligible else "missing_reference_history",
            setup_id=f"odr-v3:{session}:{side}",
            gap_direction="up",
        ),
    )


def _entry_metadata(
    *, setup_id: str = SETUP_ID, stop: float = 116.0, target: float = 95.0
) -> dict[str, Any]:
    return {
        "setup_id": setup_id,
        "branch": OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
        "gap_direction": "up",
        "signal_price": 108.0,
        "stop_price": stop,
        "target_price": target,
        "prior_rth_close": 95.0,
        "rth_open": 110.0,
        "dtr20": 100.0,
        "gap_dtr": 0.15,
        "displacement_breadth": 0.75,
        "extension_magnitude_dtr": 0.04,
        "decisive_cross_distance_dtr": 0.02,
        "close_location": 0.75,
        "structural_extreme": 114.0,
        "decision_risk_dtr": 0.08,
        "target_distance_r": 1.625,
    }


def _trade(**changes: Any) -> Trade:
    base = Trade(
        symbol="NQ1!",
        side="short",
        quantity=1,
        entry_timestamp_utc=ENTRY_TIME,
        entry_price=107.25,
        exit_timestamp_utc=EXIT_TIME,
        exit_price=95.75,
        exit_reason="overnight_displacement_reversal_time_exit",
        stop_price=116.0,
        gross_points=11.5,
        gross_pnl=230.0,
        commission=10.0,
        net_pnl=220.0,
        mfe_points=12.25,
        mae_points=2.0,
        session_date=SESSION,
    )
    return replace(base, **changes)


def _ledger(
    trade: Trade | None = None, *, include_dtr_history: bool = False
) -> EventLedger:
    trade = _trade() if trade is None else trade
    metadata = _entry_metadata(stop=trade.stop_price)
    ledger = EventLedger()
    if include_dtr_history:
        for day in HISTORY_DAYS:
            close_minute = rth_close_minutes_et(day)
            assert close_minute is not None
            for minute_et in range(570, close_minute):
                minute_utc = minute_et + 5 * 60
                timestamp = (
                    f"{day.isoformat()}T{minute_utc // 60:02d}:"
                    f"{minute_utc % 60:02d}:00Z"
                )
                ledger.append(
                    EventType.BAR,
                    timestamp_utc=timestamp,
                    payload={
                        "symbol": "NQ1!",
                        "open": 95.0,
                        "high": 145.0,
                        "low": 45.0,
                        "close": 95.0,
                        "volume": 1_000.0,
                    },
                )
        overnight_offsets = [offset for offset in range(930) if offset % 30 != 0]
        assert len(overnight_offsets) == 899
        # Keep offset zero and omit a different interior minute so coverage is
        # exactly 900 bars with a two-minute maximum gap.
        overnight_offsets.insert(0, 0)
        assert len(overnight_offsets) == 900
        overnight_start = datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc)
        for ordinal, offset in enumerate(overnight_offsets):
            close = 100.0 if ordinal < 675 else 90.0
            timestamp = overnight_start + timedelta(minutes=offset)
            ledger.append(
                EventType.BAR,
                timestamp_utc=timestamp.isoformat().replace("+00:00", "Z"),
                payload={
                    "symbol": "NQ1!",
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1.0,
                },
            )
    for minute in range(30, 36):
        timestamp = f"2024-01-02T14:{minute:02d}:00Z"
        if minute == 35:
            payload = {
                "symbol": "NQ1!",
                "open": 110.0,
                "high": 114.0,
                "low": 107.5,
                "close": 108.0,
                "volume": 1_000.0,
            }
        else:
            payload = {
                "symbol": "NQ1!",
                "open": 110.0,
                "high": 113.0,
                "low": 109.0,
                "close": 111.0,
                "volume": 1_000.0,
            }
        ledger.append(EventType.BAR, timestamp_utc=timestamp, payload=payload)
    ledger.append(
        EventType.SIGNAL_DECISION,
        timestamp_utc=SIGNAL_TIME,
        payload={
            "symbol": "NQ1!",
            "decision": "accepted",
            "side": "short",
            "reason": OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
            **metadata,
        },
    )
    ledger.append(
        EventType.ORDER_INTENT,
        timestamp_utc=SIGNAL_TIME,
        payload={
            "symbol": "NQ1!",
            "side": "sell",
            "quantity": 1,
            "order_type": "market_entry",
            "reason": OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
            **metadata,
        },
    )
    ledger.append(
        EventType.BAR,
        timestamp_utc=ENTRY_TIME,
        payload={
            "symbol": "NQ1!",
            "open": 108.0,
            "high": 109.0,
            "low": 106.0,
            "close": 107.0,
            "volume": 900.0,
        },
    )
    ledger.append(
        EventType.FILL,
        timestamp_utc=ENTRY_TIME,
        payload={
            "symbol": "NQ1!",
            "side": "sell",
            "quantity": 1,
            "price": trade.entry_price,
            "reason": OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
            "raw_price": 108.0,
            "slippage_points": 0.75,
            "ambiguous": False,
        },
    )
    ledger.append(
        EventType.STOP_UPDATE,
        timestamp_utc=ENTRY_TIME,
        payload={
            "symbol": "NQ1!",
            "stop_price": trade.stop_price,
            "reason": "initial_stop",
            "applied": True,
        },
    )
    if include_dtr_history:
        for minute_et in range(577, 11 * 60 + 59):
            minute_utc = minute_et + 5 * 60
            ledger.append(
                EventType.BAR,
                timestamp_utc=(
                    f"2024-01-02T{minute_utc // 60:02d}:"
                    f"{minute_utc % 60:02d}:00Z"
                ),
                payload={
                    "symbol": "NQ1!",
                    "open": 108.0,
                    "high": 109.0,
                    "low": 106.0,
                    "close": 108.0,
                    "volume": 500.0,
                },
            )
    ledger.append(
        EventType.BAR,
        timestamp_utc=EXIT_SIGNAL_TIME,
        payload={
            "symbol": "NQ1!",
            "open": 96.0,
            "high": 97.0,
            "low": 95.5,
            "close": 96.0,
            "volume": 500.0,
        },
    )
    ledger.append(
        EventType.EXIT,
        timestamp_utc=EXIT_SIGNAL_TIME,
        payload={"symbol": "NQ1!", "reason": trade.exit_reason},
    )
    ledger.append(
        EventType.BAR,
        timestamp_utc=EXIT_TIME,
        payload={
            "symbol": "NQ1!",
            "open": 95.0,
            "high": 96.0,
            "low": 94.0,
            "close": 95.0,
            "volume": 500.0,
        },
    )
    ledger.append(
        EventType.FILL,
        timestamp_utc=EXIT_TIME,
        payload={
            "symbol": "NQ1!",
            "side": "buy",
            "quantity": 1,
            "price": trade.exit_price,
            "reason": trade.exit_reason,
            "raw_price": 95.0,
            "slippage_points": 0.75,
            "ambiguous": False,
        },
    )
    ledger.append(
        EventType.TRADE_CLOSED,
        timestamp_utc=EXIT_TIME,
        payload=trade.to_payload(),
    )
    if include_dtr_history:
        for minute_et in range(12 * 60 + 1, 16 * 60):
            minute_utc = minute_et + 5 * 60
            ledger.append(
                EventType.BAR,
                timestamp_utc=(
                    f"2024-01-02T{minute_utc // 60:02d}:"
                    f"{minute_utc % 60:02d}:00Z"
                ),
                payload={
                    "symbol": "NQ1!",
                    "open": 95.0,
                    "high": 96.0,
                    "low": 94.0,
                    "close": 95.0,
                    "volume": 500.0,
                },
            )
    return ledger


def _diagnostics(*, fill_setup_id: str | None = SETUP_ID) -> list[SyntheticDiagnostic]:
    return [
        SyntheticDiagnostic(
            SESSION,
            "2024-01-02T14:30:00Z",
            "classified",
            SETUP_ID,
            state="wait_extension",
        ),
        SyntheticDiagnostic(
            SESSION,
            SIGNAL_TIME,
            "extension_armed",
            SETUP_ID,
            state="wait_rejection",
        ),
        SyntheticDiagnostic(
            SESSION,
            SIGNAL_TIME,
            "entry_confirmed",
            SETUP_ID,
            state="entry_pending",
        ),
        SyntheticDiagnostic(
            SESSION,
            ENTRY_TIME,
            "filled",
            fill_setup_id,
            state="position",
            metadata={"price": 107.25, "quantity": 1},
        ),
    ]


def _clone_ledger(ledger: EventLedger) -> EventLedger:
    result = EventLedger()
    result.records = list(ledger.records)
    return result


def _replace_first(
    ledger: EventLedger,
    event_type: EventType,
    transform: Any,
) -> EventLedger:
    result = _clone_ledger(ledger)
    for index, record in enumerate(result.records):
        if record.event_type == event_type:
            result.records[index] = transform(record)
            return result
    raise AssertionError(f"missing event type {event_type}")


def _audit(
    *,
    ledger: EventLedger | None = None,
    trades: list[Trade] | None = None,
    diagnostics: list[SyntheticDiagnostic] | None = None,
    delay: int = 0,
) -> dict[str, Any]:
    return audit_overnight_displacement_reconciliation(
        ledger=_ledger() if ledger is None else ledger,
        trades=[_trade()] if trades is None else trades,
        diagnostic_events=_diagnostics() if diagnostics is None else diagnostics,
        point_value=20.0,
        expected_entry_delay_bars=delay,
    )


def test_clean_setup_id_pipeline_reconciles_exactly() -> None:
    audit = _audit()
    assert audit["violation_count"] == 0, audit["violations"]
    attributed = attribute_overnight_displacement_trades([_trade()], _ledger())
    assert len(attributed) == 1
    assert attributed[0].entry.setup_id == SETUP_ID
    assert attributed[0].entry.signal_price == 108.0
    assert attributed[0].entry.target_price == 95.0


def test_report_scores_every_calendar_zero_and_post_cost_net_r() -> None:
    report = build_overnight_displacement_reversal_report(
        trades=[_trade()],
        ledger=_ledger(include_dtr_history=True),
        snapshots=[_snapshot()],
        diagnostic_events=_diagnostics(),
        point_value=20.0,
        score_start_session="2024-01-01",
        score_end_session_exclusive="2025-01-01",
        allocated_capital=100_000.0,
        hard_loss_limit=20_000.0,
    )
    assert report["score_window"]["causal_warmup_ready_ordinal"] == 1
    assert report["score_window"]["expected_score_sessions_after_warmup"] == 259
    assert report["score_window"]["missing_expected_session_count"] == 258
    assert report["daily"]["trading_days"] == 259
    assert report["daily"]["days_with_trades"] == 1
    assert report["weekly"]["week_count"] == 53
    expected_net_r = 220.0 / ((116.0 - 107.25) * 20.0)
    assert report["fill_relative_trades"][0]["realized_net_r"] == pytest.approx(expected_net_r)
    assert report["weekly"]["realized_net_r"]["mean"] == pytest.approx(expected_net_r / 53)
    assert report["execution_diagnostics"]["reconciliation_violation_count"] == 0
    assert report["bootstrap"]["draws"] == 20_000
    assert report["bootstrap"]["block_length_sessions"] == 10
    assert report["bootstrap"]["seed"] == 20260712
    assert report["allocated_capital_returns"]["status"] == "available"
    assert report["portfolio_overlap"]["status"] == "unavailable_no_synchronized_comparison_series"
    for key in (
        "extension_magnitude_dtr",
        "decisive_cross_distance_dtr",
        "close_location",
        "structural_extreme",
        "decision_risk_dtr",
        "target_distance_r",
    ):
        assert report["feature_distributions"][key]["count"] == 1
    assert report["promotion_status"] == "rejected_primary_no_threshold_rescue"


def test_warmup_gate_is_explicitly_bounded_at_25_expected_sessions() -> None:
    minimal_report = {
        "overall": {"survivability": {"trade_count": 0, "net_pnl": 0.0, "expectancy_per_trade": 0.0, "profit_factor": None, "pnl_without_top_5_trades": 0.0}},
        "by_side": {},
        "daily": {"trading_days": 0, "sharpe_annualized": 0.0, "pnl_without_top_5_days": 0.0, "top_5_day_share": None},
        "weekly": {"realized_net_r": {"mean": None}, "net_pnl": {"mean": None}},
        "bootstrap": {"probability_total_net_nonpositive": 1.0, "max_drawdown_p95_adverse": 0.0, "max_drawdown_p99_adverse": 0.0, "draws": 20_000},
        "complete_half_years": {},
        "fill_relative_trades": [],
        "risk_efficiency": {"observed_annualized_net_pnl": 0.0},
        "deterministic_replay": {"verified": False},
        "score_window": {
            "causal_warmup_ready_ordinal": 25,
            "missing_expected_session_count": 0,
            "unexpected_snapshot_session_count": 0,
            "active_rth_minute_gap_session_count": 0,
            "expected_score_sessions_after_warmup": 0,
        },
        "mechanism_counts": {OVERNIGHT_DISPLACEMENT_REVERSAL_REASON: 0},
        "execution_diagnostics": {"reconciliation_violation_count": 0},
    }
    gates = evaluate_odr_t1_primary_gates(minimal_report)
    assert gates["checks"]["causal_warmup_completed_within_25_expected_sessions"] is True
    late = deepcopy(minimal_report)
    late["score_window"]["causal_warmup_ready_ordinal"] = 26
    assert evaluate_odr_t1_primary_gates(late)["checks"]["causal_warmup_completed_within_25_expected_sessions"] is False


def test_missing_signal_setup_id_is_a_violation() -> None:
    ledger = _replace_first(
        _ledger(),
        EventType.SIGNAL_DECISION,
        lambda record: replace(record, payload={key: value for key, value in record.payload.items() if key != "setup_id"}),
    )
    assert any("signal_missing_setup_id" in item for item in _audit(ledger=ledger)["violations"])


def test_signal_and_intent_setup_ids_must_match() -> None:
    ledger = _replace_first(
        _ledger(),
        EventType.ORDER_INTENT,
        lambda record: replace(record, payload={**record.payload, "setup_id": "odr-v3:2024-01-02:long"}),
    )
    violations = _audit(ledger=ledger)["violations"]
    assert "signal_intent_setup_id_set_mismatch" in violations


def test_all_shared_signal_intent_metadata_must_match() -> None:
    ledger = _replace_first(
        _ledger(),
        EventType.ORDER_INTENT,
        lambda record: replace(record, payload={**record.payload, "gap_dtr": 0.20}),
    )
    assert f"signal_intent_full_metadata_mismatch:{SETUP_ID}" in _audit(ledger=ledger)["violations"]


@pytest.mark.parametrize("target", [107.25, 108.0])
def test_short_target_equal_to_or_behind_actual_fill_is_fatal(target: float) -> None:
    ledger = _ledger()
    for index, record in enumerate(ledger.records):
        if record.event_type in (EventType.SIGNAL_DECISION, EventType.ORDER_INTENT):
            ledger.records[index] = replace(record, payload={**record.payload, "target_price": target})
    violations = _audit(ledger=ledger)["violations"]
    assert f"target_not_strictly_ahead_at_fill:{SETUP_ID}" in violations


@pytest.mark.parametrize("stop", [107.25, 107.0])
def test_short_stop_equal_to_or_behind_actual_fill_is_fatal(stop: float) -> None:
    trade = _trade(stop_price=stop)
    ledger = _ledger(trade)
    violations = _audit(ledger=ledger, trades=[trade])["violations"]
    assert f"stop_not_strictly_protective_at_fill:{SETUP_ID}" in violations


def test_risk_veto_of_accepted_setup_is_fatal() -> None:
    ledger = _replace_first(
        _ledger(),
        EventType.ORDER_INTENT,
        lambda record: replace(record, event_type=EventType.RISK_VETO, payload={**record.payload, "veto_reason": "invalid_stop"}),
    )
    assert "accepted_entry_risk_veto" in _audit(ledger=ledger)["violations"]


def test_engine_stop_gap_invalidation_is_fatal() -> None:
    ledger = _replace_first(
        _ledger(),
        EventType.FILL,
        lambda record: replace(
            record,
            event_type=EventType.STATE_TRANSITION,
            payload={
                "transition": "entry_invalidated_at_fill",
                "reason": "stop_not_protective_at_fill",
                "symbol": "NQ1!",
                "side": "short",
                "intent_timestamp_utc": SIGNAL_TIME,
            },
        ),
    )
    violations = _audit(ledger=ledger, trades=[])["violations"]
    assert "entry_invalidated_at_fill" in violations
    assert "accepted_intent_terminal_without_fill" in violations


def test_fill_requires_diagnostic_setup_id() -> None:
    violations = _audit(diagnostics=_diagnostics(fill_setup_id=None))["violations"]
    assert any("diagnostic_fill" in item and "setup_id" in item for item in violations)


def test_rejected_decision_matches_diagnostic_and_never_emits_intent() -> None:
    ledger = EventLedger()
    for minute in range(30, 36):
        ledger.append(
            EventType.BAR,
            timestamp_utc=f"2024-01-02T14:{minute:02d}:00Z",
            payload={
                "symbol": "NQ1!",
                "open": 110.0,
                "high": 114.0,
                "low": 107.0,
                "close": 108.0,
                "volume": 1_000.0,
            },
        )
    ledger.append(
        EventType.SIGNAL_DECISION,
        timestamp_utc=SIGNAL_TIME,
        payload={
            "symbol": "NQ1!",
            "decision": "rejected",
            "side": "short",
            "reason": "decision_risk_geometry",
            "setup_id": SETUP_ID,
            "branch": OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
            "gap_direction": "up",
            "decision_risk_points": 25.0,
            "decision_risk_dtr": 0.25,
        },
    )
    diagnostics = [
        SyntheticDiagnostic(
            SESSION,
            SIGNAL_TIME,
            "entry_rejected",
            SETUP_ID,
            state="done",
            metadata={"reason": "decision_risk_geometry"},
        )
    ]
    audit = _audit(ledger=ledger, trades=[], diagnostics=diagnostics)
    assert audit["rejected_signal_count"] == 1
    assert audit["violation_count"] == 0, audit["violations"]


def test_next_bar_fill_timing_is_exact() -> None:
    ledger = _replace_first(
        _ledger(),
        EventType.FILL,
        lambda record: replace(record, timestamp_utc="2024-01-02T14:37:00Z"),
    )
    assert f"entry_fill_timing_mismatch:{SETUP_ID}" in _audit(ledger=ledger)["violations"]


def test_fill_quantity_must_match_order_and_trade() -> None:
    ledger = _replace_first(
        _ledger(),
        EventType.FILL,
        lambda record: replace(record, payload={**record.payload, "quantity": 2}),
    )
    assert f"intent_fill_quantity_mismatch:{SETUP_ID}" in _audit(ledger=ledger)["violations"]


def test_frozen_entry_and_exit_cost_economics_are_exact() -> None:
    ledger = _ledger()
    entry_index = next(
        index
        for index, record in enumerate(ledger.records)
        if record.event_type == EventType.FILL
        and record.payload.get("reason") == OVERNIGHT_DISPLACEMENT_REVERSAL_REASON
    )
    entry = ledger.records[entry_index]
    ledger.records[entry_index] = replace(
        entry,
        payload={
            **entry.payload,
            "price": 107.0,
            "raw_price": 107.5,
            "slippage_points": 0.5,
        },
    )
    violations = _audit(ledger=ledger)["violations"]
    assert f"entry_fill_raw_price_bar_open_mismatch:{SETUP_ID}" in violations
    assert f"entry_fill_slippage_mismatch:{SETUP_ID}" in violations
    assert f"entry_fill_price_economics_mismatch:{SETUP_ID}" in violations


def test_commission_is_exactly_ten_dollars_per_contract() -> None:
    corrupt = _trade(commission=9.0, net_pnl=221.0)
    assert "trade_commission_mismatch_1" in _audit(trades=[corrupt])["violations"]


def test_signal_stop_and_target_must_be_tick_aligned() -> None:
    ledger = _ledger()
    for index, record in enumerate(ledger.records):
        if record.event_type in (EventType.SIGNAL_DECISION, EventType.ORDER_INTENT):
            ledger.records[index] = replace(record, payload={**record.payload, "target_price": 95.1})
    assert f"target_not_tick_aligned:{SETUP_ID}" in _audit(ledger=ledger)["violations"]


def test_time_exit_is_exactly_11_59_to_12_00_et() -> None:
    ledger = _replace_first(
        _ledger(),
        EventType.EXIT,
        lambda record: replace(record, timestamp_utc="2024-01-02T16:58:00Z"),
    )
    violations = _audit(ledger=ledger)["violations"]
    assert "time_exit_decision_not_11_59_et_1" in violations


def test_target_cannot_win_same_bar_when_stop_also_touched() -> None:
    trade = replace(_trade(), exit_reason="target")
    ledger = _ledger(trade)
    ledger.records = [record for record in ledger.records if record.event_type != EventType.EXIT]
    exit_bar_index = next(
        index
        for index, record in enumerate(ledger.records)
        if record.event_type == EventType.BAR and record.timestamp_utc == EXIT_TIME
    )
    exit_bar = ledger.records[exit_bar_index]
    ledger.records[exit_bar_index] = replace(
        exit_bar,
        payload={**exit_bar.payload, "high": 117.0, "low": 94.0},
    )
    violations = _audit(ledger=ledger, trades=[trade])["violations"]
    assert f"target_selected_despite_same_bar_stop_touch:{SETUP_ID}" in violations


def test_same_bar_stop_and_target_requires_ambiguous_flag() -> None:
    trade = _trade(
        exit_price=116.75,
        exit_reason="stop",
        gross_points=-9.5,
        gross_pnl=-190.0,
        net_pnl=-200.0,
    )
    ledger = _ledger(trade)
    ledger.records = [record for record in ledger.records if record.event_type != EventType.EXIT]
    exit_bar_index = next(
        index
        for index, record in enumerate(ledger.records)
        if record.event_type == EventType.BAR and record.timestamp_utc == EXIT_TIME
    )
    exit_bar = ledger.records[exit_bar_index]
    ledger.records[exit_bar_index] = replace(
        exit_bar,
        payload={**exit_bar.payload, "open": 110.0, "high": 117.0, "low": 94.0},
    )
    exit_fill_index = next(
        index
        for index, record in enumerate(ledger.records)
        if record.event_type == EventType.FILL and record.payload.get("reason") == "stop"
    )
    exit_fill = ledger.records[exit_fill_index]
    ledger.records[exit_fill_index] = replace(
        exit_fill,
        payload={
            **exit_fill.payload,
            "price": 116.75,
            "raw_price": 116.0,
            "ambiguous": False,
        },
    )
    trade_record_index = next(
        index for index, record in enumerate(ledger.records) if record.event_type == EventType.TRADE_CLOSED
    )
    ledger.records[trade_record_index] = replace(
        ledger.records[trade_record_index], payload=trade.to_payload()
    )
    violations = _audit(ledger=ledger, trades=[trade])["violations"]
    assert f"same_bar_stop_target_not_flagged_ambiguous:{SETUP_ID}" in violations


def test_trade_entry_price_and_pnl_are_recomputed() -> None:
    corrupt = _trade(entry_price=107.0, net_pnl=999.0)
    violations = _audit(trades=[corrupt])["violations"]
    assert "trade_entry_price_mismatch_1" in violations
    assert "trade_net_pnl_mismatch_1" in violations


def test_exit_side_and_trade_record_payload_are_exact() -> None:
    ledger = _ledger()
    exit_fill_index = next(
        index
        for index, record in enumerate(ledger.records)
        if record.event_type == EventType.FILL
        and record.payload.get("reason") != OVERNIGHT_DISPLACEMENT_REVERSAL_REASON
    )
    exit_fill = ledger.records[exit_fill_index]
    ledger.records[exit_fill_index] = replace(exit_fill, payload={**exit_fill.payload, "side": "sell"})
    trade_record_index = next(
        index for index, record in enumerate(ledger.records) if record.event_type == EventType.TRADE_CLOSED
    )
    trade_record = ledger.records[trade_record_index]
    ledger.records[trade_record_index] = replace(trade_record, payload={**trade_record.payload, "net_pnl": 999.0})
    violations = _audit(ledger=ledger)["violations"]
    assert "trade_exit_side_mismatch_1" in violations
    assert "trade_ledger_payload_mismatch_1" in violations


def test_more_than_one_attempt_per_session_is_fatal() -> None:
    ledger = _ledger()
    signal = next(record for record in ledger.records if record.event_type == EventType.SIGNAL_DECISION)
    ledger.append(EventType.SIGNAL_DECISION, timestamp_utc="2024-01-02T14:40:00Z", payload=signal.payload)
    assert "more_than_one_entry_attempt_in_session" in _audit(ledger=ledger)["violations"]


def test_active_rth_gap_is_detected_and_blocks_gate() -> None:
    diagnostics = _diagnostics()
    diagnostics.append(
        SyntheticDiagnostic(
            SESSION,
            "2024-01-02T14:37:00Z",
            "entry_cancelled",
            SETUP_ID,
            state="done",
            metadata={"reason": "active_rth_minute_gap"},
        )
    )
    report = build_overnight_displacement_reversal_report(
        trades=[_trade()],
        ledger=_ledger(include_dtr_history=True),
        snapshots=[_snapshot()],
        diagnostic_events=diagnostics,
        point_value=20.0,
        score_start_session="2024-01-01",
        score_end_session_exclusive="2024-02-01",
    )
    assert report["score_window"]["active_rth_minute_gap_session_count"] == 1
    assert "active_rth_minute_gap" in report["execution_diagnostics"]["reconciliation_violations"]
    assert report["t1_primary_gates"]["checks"]["zero_active_rth_minute_gap_sessions"] is False


def test_bar_ledger_detects_forged_dtr_prior_close_and_overnight_values() -> None:
    clean = _snapshot()
    corrupted = SyntheticSnapshot(
        replace(
            clean.features,
            prior_rth_close=96.0,
            overnight_vwap=999.0,
            dtr_values=(99.0,) * 20,
        ),
        clean.classification,
    )
    report = build_overnight_displacement_reversal_report(
        trades=[_trade()],
        ledger=_ledger(include_dtr_history=True),
        snapshots=[corrupted],
        diagnostic_events=_diagnostics(),
        point_value=20.0,
        score_start_session="2024-01-01",
        score_end_session_exclusive="2024-02-01",
    )
    violations = report["execution_diagnostics"]["reconciliation_violations"]
    assert f"prior_rth_close_bar_ledger_mismatch:{SESSION}" in violations
    assert f"overnight_vwap_bar_ledger_mismatch:{SESSION}" in violations
    assert f"dtr_source_values_bar_ledger_mismatch:{SESSION}" in violations


def test_missing_active_bar_fails_even_when_strategy_forgot_diagnostic() -> None:
    ledger = _ledger(include_dtr_history=True)
    ledger.records = [
        record
        for record in ledger.records
        if not (
            record.event_type == EventType.BAR
            and record.timestamp_utc == "2024-01-02T14:33:00Z"
        )
    ]
    report = build_overnight_displacement_reversal_report(
        trades=[_trade()],
        ledger=ledger,
        snapshots=[_snapshot()],
        diagnostic_events=_diagnostics(),
        point_value=20.0,
        score_start_session="2024-01-01",
        score_end_session_exclusive="2024-02-01",
    )
    audit = report["score_window"]["active_rth_continuity_audit"]
    assert audit["reconstructed_gap_sessions"] == [SESSION]
    assert audit["diagnostic_missing_for_reconstructed_gap"] == [SESSION]
    assert "active_rth_gap_missing_strategy_diagnostic" in report["execution_diagnostics"]["reconciliation_violations"]


def test_silent_eligible_session_has_no_valid_terminal_state() -> None:
    diagnostics = [
        SyntheticDiagnostic(
            SESSION,
            "2024-01-02T14:30:00Z",
            "classified",
            SETUP_ID,
            state="wait_extension",
        )
    ]
    report = build_overnight_displacement_reversal_report(
        trades=[_trade()],
        ledger=_ledger(include_dtr_history=True),
        snapshots=[_snapshot()],
        diagnostic_events=diagnostics,
        point_value=20.0,
        score_start_session="2024-01-01",
        score_end_session_exclusive="2024-02-01",
    )
    assert f"eligible_session_terminal_outcome_count_mismatch:{SESSION}" in report[
        "execution_diagnostics"
    ]["reconciliation_violations"]


def test_pre_warmup_trade_is_fatal_but_report_remains_serializable() -> None:
    warmup = SyntheticSnapshot(
        replace(
            _snapshot().features,
            dtr20=None,
            dtr_session_dates=(),
            dtr_values=(),
        ),
        SyntheticClassification(
            regime="no_trade",
            side="none",
            reason="missing_reference_history",
            setup_id="odr-v3:2024-01-02:none",
            gap_direction="up",
        ),
    )
    ready = _snapshot("2024-01-03")
    report = build_overnight_displacement_reversal_report(
        trades=[_trade()],
        ledger=_ledger(include_dtr_history=True),
        snapshots=[warmup, ready],
        diagnostic_events=[],
        point_value=20.0,
        score_start_session="2024-01-01",
        score_end_session_exclusive="2024-02-01",
    )
    assert report["overall"]["survivability"]["trade_count"] == 0
    assert any(
        item.startswith("trades_before_effective_warmup_or_outside_score_window:")
        for item in report["execution_diagnostics"]["reconciliation_violations"]
    )


@pytest.mark.parametrize(
    "allocated,hard_limit",
    [(100_000.0, None), (None, 20_000.0), (0.0, 20_000.0), (100_000.0, -1.0)],
)
def test_capital_policy_inputs_fail_closed(
    allocated: float | None, hard_limit: float | None
) -> None:
    with pytest.raises(ValueError):
        build_overnight_displacement_reversal_report(
            trades=[],
            ledger=EventLedger(),
            snapshots=[],
            diagnostic_events=[],
            point_value=20.0,
            score_start_session="2024-01-01",
            score_end_session_exclusive="2024-02-01",
            allocated_capital=allocated,
            hard_loss_limit=hard_limit,
        )
