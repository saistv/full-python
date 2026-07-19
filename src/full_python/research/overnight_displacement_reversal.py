"""Research attribution, diagnostics, and frozen T1 gates for ODR v3.

The module deliberately depends only on generic calendar, reporting, event, and
model primitives.  Strategy objects are consumed through their public frozen
fields so the research layer cannot reuse v1/v2 classifier logic.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
import statistics
from typing import Any, Iterable, Optional, Sequence

from full_python.data.exchange_calendar import rth_close_minutes_et
from full_python.data.databento import front_contract_for_session
from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger, EventRecord, EventType
from full_python.models import Trade
from full_python.reporting.bootstrap import build_block_bootstrap_report
from full_python.reporting.metrics import build_metrics_report
from full_python.reporting.survivability import (
    TradeResult,
    build_daily_metrics,
    build_survivability_report,
)
from full_python.research.statistical_confidence import build_sharpe_confidence


OVERNIGHT_DISPLACEMENT_REVERSAL_REASON = "overnight_displacement_reversal"
ENTRY_REASONS = (OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,)
SETUP_PREFIX = "odr-v3:"
_TERMINAL_ENTRY_TRANSITIONS = {
    "entry_invalidated_at_fill",
    "entry_missed",
    "pending_orders_cancelled",
}
_ENGINE_EXIT_REASONS = {
    "stop",
    "target",
    "stop_gap",
    "session_flatten",
    "session_end",
    "end_of_data",
    "daily_limit",
}


@dataclass(frozen=True)
class ODREntryAttribution:
    setup_id: str
    entry_timestamp_utc: str
    symbol: str
    side: str
    quantity: int
    fill_price: float
    entry_reason: str
    signal_timestamp_utc: str
    signal_price: float
    stop_price: float
    target_price: float
    prior_rth_close: float
    rth_open: float
    dtr20: float
    gap_direction: str
    gap_dtr: Optional[float]
    displacement_breadth: Optional[float]


@dataclass(frozen=True)
class AttributedODRTrade:
    trade: Trade
    entry: ODREntryAttribution


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _metadata(event: Any) -> dict[str, Any]:
    value = _field(event, "metadata", {})
    return value if isinstance(value, dict) else {}


def _diagnostic_setup_id(event: Any) -> Optional[str]:
    value = _field(event, "setup_id")
    if value is None:
        value = _metadata(event).get("setup_id")
    return None if value is None else str(value)


def _snapshot_session(snapshot: Any) -> str:
    return str(_field(_field(snapshot, "features"), "session_date"))


def _classification_eligible(snapshot: Any) -> bool:
    classification = _field(snapshot, "classification")
    regime = str(_enum_value(_field(classification, "regime", "no_trade")))
    side = str(_enum_value(_field(classification, "side", "none")))
    return regime != "no_trade" and side in ("long", "short")


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _warmup_ready(snapshot: Any) -> bool:
    features = _field(snapshot, "features")
    dtr = _field(features, "dtr20")
    return bool(
        _field(features, "prior_rth_complete", False)
        and _finite(dtr)
        and float(dtr) > 0
        and _finite(_field(features, "prior_rth_close"))
    )


def _feature_ready(snapshot: Any) -> bool:
    features = _field(snapshot, "features")
    required = (
        _field(features, "dtr20"),
        _field(features, "prior_rth_close"),
        _field(features, "rth_open"),
        _field(features, "gap_signed_points"),
        _field(features, "gap_dtr"),
        _field(features, "displacement_breadth"),
    )
    return bool(
        _field(features, "current_rth_session", False)
        and _field(features, "prior_rth_complete", False)
        and _field(features, "complete_overnight", False)
        and not _field(features, "roll_transition", False)
        and all(_finite(value) for value in required)
        and float(_field(features, "dtr20")) > 0
    )


def _expected_cme_sessions(start_session: str, end_session_exclusive: str) -> list[str]:
    cursor = date.fromisoformat(start_session)
    end = date.fromisoformat(end_session_exclusive)
    sessions: list[str] = []
    while cursor < end:
        if rth_close_minutes_et(cursor) is not None:
            sessions.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return sessions


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _quantile(values: Sequence[float], probability: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    finite_values = [float(value) for value in values if _finite(value)]
    return {
        "count": len(finite_values),
        "min": min(finite_values, default=None),
        "p05": _quantile(finite_values, 0.05),
        "p25": _quantile(finite_values, 0.25),
        "median": _quantile(finite_values, 0.50),
        "mean": statistics.mean(finite_values) if finite_values else None,
        "p75": _quantile(finite_values, 0.75),
        "p95": _quantile(finite_values, 0.95),
        "max": max(finite_values, default=None),
    }


def _t_stat(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    deviation = statistics.stdev(values)
    if deviation == 0:
        return None
    return statistics.mean(values) / (deviation / math.sqrt(len(values)))


def _summary(
    trades: Sequence[Trade], *, point_value: float, score_rth_minutes: int = 0
) -> dict[str, Any]:
    survivability = build_survivability_report(
        [TradeResult(item.exit_timestamp_utc, item.side, item.net_pnl) for item in trades]
    )
    risks = [abs(item.entry_price - item.stop_price) for item in trades]
    mfe_r = [item.mfe_points / risk for item, risk in zip(trades, risks) if risk > 0]
    mae_r = [item.mae_points / risk for item, risk in zip(trades, risks) if risk > 0]
    holding = [
        (_parse_utc(item.exit_timestamp_utc) - _parse_utc(item.entry_timestamp_utc)).total_seconds()
        / 60.0
        for item in trades
    ]
    total_holding = sum(holding)
    return {
        "survivability": survivability.to_dict(),
        "metrics": build_metrics_report(trades, point_value=point_value).to_dict(),
        "trade_pnl_dollars": _distribution(item.net_pnl for item in trades),
        "trade_t_stat": _t_stat([item.net_pnl for item in trades]),
        "holding_time_minutes": {
            "mean": statistics.mean(holding) if holding else None,
            "median": statistics.median(holding) if holding else None,
            "max": max(holding, default=None),
            "total": total_holding,
        },
        "exposure_fraction_of_rth_minutes": (
            total_holding / score_rth_minutes
            if score_rth_minutes > 0
            else None
        ),
        "excursions": {
            "mean_mfe_points": statistics.mean(item.mfe_points for item in trades)
            if trades
            else None,
            "median_mfe_points": statistics.median(item.mfe_points for item in trades)
            if trades
            else None,
            "mean_mae_points": statistics.mean(item.mae_points for item in trades)
            if trades
            else None,
            "median_mae_points": statistics.median(item.mae_points for item in trades)
            if trades
            else None,
            "mean_mfe_r": statistics.mean(mfe_r) if mfe_r else None,
            "median_mfe_r": statistics.median(mfe_r) if mfe_r else None,
            "mean_mae_r": statistics.mean(mae_r) if mae_r else None,
            "median_mae_r": statistics.median(mae_r) if mae_r else None,
        },
    }


def _grouped_trades(
    trades: Sequence[Trade], *, key: Any, point_value: float
) -> dict[str, Any]:
    groups: dict[str, list[Trade]] = {}
    for trade in trades:
        groups.setdefault(str(key(trade)), []).append(trade)
    return {
        name: _summary(group, point_value=point_value)
        for name, group in sorted(groups.items())
    }


def _weekly_report(
    *,
    session_dates: Sequence[str],
    daily_pnl: dict[str, float],
    realized_r_by_session: dict[str, float],
) -> dict[str, Any]:
    pnl: dict[str, float] = {}
    r_values: dict[str, float] = {}
    for day in session_dates:
        parsed = date.fromisoformat(day)
        iso_year, iso_week, _ = parsed.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        pnl[key] = pnl.get(key, 0.0) + daily_pnl.get(day, 0.0)
        r_values[key] = r_values.get(key, 0.0) + realized_r_by_session.get(day, 0.0)
    weeks = sorted(pnl)
    pnl_series = [pnl[key] for key in weeks]
    r_series = [r_values[key] for key in weeks]
    return {
        "week_count": len(weeks),
        "weeks": [
            {"week": key, "net_pnl": pnl[key], "realized_net_r": r_values[key]}
            for key in weeks
        ],
        "net_pnl": {
            **_distribution(pnl_series),
            "positive_week_rate": (
                sum(value > 0 for value in pnl_series) / len(pnl_series)
                if pnl_series
                else 0.0
            ),
        },
        "realized_net_r": {
            **_distribution(r_series),
            "positive_week_rate": (
                sum(value > 0 for value in r_series) / len(r_series)
                if r_series
                else 0.0
            ),
        },
        "rolling_13_week": {
            "net_pnl": _distribution(
                sum(pnl_series[index - 12 : index + 1])
                for index in range(12, len(pnl_series))
            ),
            "realized_net_r": _distribution(
                sum(r_series[index - 12 : index + 1])
                for index in range(12, len(r_series))
            ),
        },
    }


def _complete_half_years(
    *,
    score_start_session: str,
    score_end_session_exclusive: str,
    session_dates: Sequence[str],
    daily_pnl: dict[str, float],
    trades: Sequence[Trade],
    point_value: float,
) -> dict[str, Any]:
    start = date.fromisoformat(score_start_session)
    end = date.fromisoformat(score_end_session_exclusive)
    folds: dict[str, Any] = {}
    for year in range(start.year, end.year + 1):
        for half, fold_start, fold_end in (
            (1, date(year, 1, 1), date(year, 7, 1)),
            (2, date(year, 7, 1), date(year + 1, 1, 1)),
        ):
            if fold_start < start or fold_end > end:
                continue
            key = f"{year}-H{half}"
            days = [
                day
                for day in session_dates
                if fold_start <= date.fromisoformat(day) < fold_end
            ]
            fold_trades = [
                trade
                for trade in trades
                if fold_start <= date.fromisoformat(trade.session_date) < fold_end
            ]
            fold_rth_minutes = sum(
                max(0, int(rth_close_minutes_et(date.fromisoformat(day)) or 570) - 570)
                for day in days
            )
            folds[key] = {
                "start_session": fold_start.isoformat(),
                "end_session_exclusive": fold_end.isoformat(),
                "eligible_sessions": len(days),
                "net_pnl_including_zero_days": sum(daily_pnl[day] for day in days),
                **_summary(
                    fold_trades,
                    point_value=point_value,
                    score_rth_minutes=fold_rth_minutes,
                ),
            }
    return folds


def _pf_pass(summary: dict[str, Any], threshold: float) -> bool:
    survivability = summary.get("survivability", {})
    if survivability.get("trade_count", 0) <= 0:
        return False
    value = survivability.get("profit_factor")
    return bool(
        (value is None and survivability.get("net_pnl", 0.0) > 0)
        or (value is not None and float(value) >= threshold)
    )


def _portfolio_overlap_report(
    *,
    session_dates: Sequence[str],
    candidate_daily_pnl: dict[str, float],
    comparison_daily_pnl: Optional[dict[str, float]],
    comparison_name: Optional[str],
    comparison_provenance: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if comparison_daily_pnl is None:
        return {
            "status": "unavailable_no_synchronized_comparison_series",
            "selection_gate": False,
        }
    if not comparison_name or not comparison_provenance:
        return {
            "status": "unavailable_missing_comparison_name_or_provenance",
            "selection_gate": False,
        }
    if any(not _finite(value) for value in comparison_daily_pnl.values()):
        raise ValueError("comparison daily P&L values must be finite")
    candidate = [candidate_daily_pnl.get(day, 0.0) for day in session_dates]
    comparison = [float(comparison_daily_pnl.get(day, 0.0)) for day in session_dates]
    correlation: Optional[float] = None
    if len(candidate) >= 2 and statistics.stdev(candidate) > 0 and statistics.stdev(comparison) > 0:
        correlation = statistics.correlation(candidate, comparison)
    candidate_fire = {day for day in session_dates if candidate_daily_pnl.get(day, 0.0) != 0}
    comparison_fire = {day for day in session_dates if comparison_daily_pnl.get(day, 0.0) != 0}
    union = candidate_fire | comparison_fire
    return {
        "status": "available_diagnostic_only",
        "selection_gate": False,
        "comparison_name": comparison_name,
        "comparison_provenance": comparison_provenance,
        "session_count": len(session_dates),
        "daily_pnl_correlation": correlation,
        "candidate_fire_days": len(candidate_fire),
        "comparison_fire_days": len(comparison_fire),
        "overlap_fire_days": len(candidate_fire & comparison_fire),
        "fire_day_jaccard": len(candidate_fire & comparison_fire) / len(union) if union else None,
        "overlap_share_of_candidate_fire_days": (
            len(candidate_fire & comparison_fire) / len(candidate_fire)
            if candidate_fire
            else None
        ),
    }


def _intent_payload(record: EventRecord) -> dict[str, Any]:
    return record.payload


def _setup_id(payload: dict[str, Any]) -> Optional[str]:
    value = payload.get("setup_id")
    return None if value is None else str(value)


def _entry_attributions(ledger: EventLedger) -> list[ODREntryAttribution]:
    pending: list[EventRecord] = []
    result: list[ODREntryAttribution] = []
    for record in ledger.records:
        payload = record.payload
        if (
            record.event_type == EventType.ORDER_INTENT
            and payload.get("reason") in ENTRY_REASONS
        ):
            pending.append(record)
            continue
        if (
            record.event_type == EventType.STATE_TRANSITION
            and payload.get("transition") in _TERMINAL_ENTRY_TRANSITIONS
        ):
            intent_timestamp = payload.get("intent_timestamp_utc")
            for index, intent in enumerate(pending):
                if intent_timestamp is None or intent.timestamp_utc == intent_timestamp:
                    pending.pop(index)
                    break
            continue
        if record.event_type != EventType.FILL or payload.get("reason") not in ENTRY_REASONS:
            continue
        if not pending:
            raise ValueError("entry fill has no attributable ODR order intent")
        intent = pending.pop(0)
        intended = intent.payload
        side = "long" if payload.get("side") == "buy" else "short"
        intended_side = "long" if intended.get("side") == "buy" else "short"
        if side != intended_side:
            raise ValueError("entry fill side does not match ODR order intent")
        if payload.get("symbol") != intended.get("symbol"):
            raise ValueError("entry fill symbol does not match ODR order intent")
        if payload.get("quantity") != intended.get("quantity"):
            raise ValueError("entry fill quantity does not match ODR order intent")
        setup_id = _setup_id(intended)
        if setup_id is None:
            raise ValueError("ODR order intent is missing setup_id")
        if payload.get("setup_id") is not None and str(payload["setup_id"]) != setup_id:
            raise ValueError("entry fill setup_id does not match ODR order intent")
        result.append(
            ODREntryAttribution(
                setup_id=setup_id,
                entry_timestamp_utc=record.timestamp_utc,
                symbol=str(payload["symbol"]),
                side=side,
                quantity=int(payload["quantity"]),
                fill_price=float(payload["price"]),
                entry_reason=str(payload["reason"]),
                signal_timestamp_utc=intent.timestamp_utc,
                signal_price=float(intended["signal_price"]),
                stop_price=float(intended["stop_price"]),
                target_price=float(intended["target_price"]),
                prior_rth_close=float(intended["prior_rth_close"]),
                rth_open=float(intended["rth_open"]),
                dtr20=float(intended["dtr20"]),
                gap_direction=str(intended["gap_direction"]),
                gap_dtr=(
                    float(intended["gap_dtr"])
                    if _finite(intended.get("gap_dtr"))
                    else None
                ),
                displacement_breadth=(
                    float(intended["displacement_breadth"])
                    if _finite(intended.get("displacement_breadth"))
                    else None
                ),
            )
        )
    return result


def attribute_overnight_displacement_trades(
    trades: Sequence[Trade], ledger: EventLedger
) -> tuple[AttributedODRTrade, ...]:
    entries: dict[tuple[str, str, str], ODREntryAttribution] = {}
    for entry in _entry_attributions(ledger):
        key = (entry.entry_timestamp_utc, entry.symbol, entry.side)
        if key in entries:
            raise ValueError(f"duplicate ODR entry attribution: {key}")
        entries[key] = entry
    attributed: list[AttributedODRTrade] = []
    for trade in trades:
        key = (trade.entry_timestamp_utc, trade.symbol, trade.side)
        entry = entries.pop(key, None)
        if entry is None:
            raise ValueError(f"trade has no ODR entry attribution: {key}")
        if trade.quantity != entry.quantity:
            raise ValueError(f"trade quantity does not match ODR entry: {key}")
        if not math.isclose(trade.entry_price, entry.fill_price, abs_tol=1e-9):
            raise ValueError(f"trade entry price does not match ODR fill: {key}")
        if not math.isclose(trade.stop_price, entry.stop_price, abs_tol=1e-9):
            raise ValueError(f"trade stop does not match frozen ODR intent: {key}")
        attributed.append(AttributedODRTrade(trade, entry))
    if entries:
        raise ValueError("filled ODR entries did not all produce closed trades")
    return tuple(attributed)


def _expected_setup_id(record: EventRecord) -> Optional[str]:
    side = record.payload.get("side")
    if side not in ("long", "short"):
        return None
    session_date = classify_timestamp(record.timestamp_utc).session_date.isoformat()
    return f"{SETUP_PREFIX}{session_date}:{side}"


def audit_overnight_displacement_reconciliation(
    *,
    ledger: EventLedger,
    trades: Sequence[Trade],
    diagnostic_events: Sequence[Any],
    point_value: float,
    expected_entry_delay_bars: int,
    commission_per_contract_round_trip: float = 10.0,
    entry_slippage_points: float = 0.75,
    exit_slippage_points: float = 0.75,
    tick_size: float = 0.25,
    attribution_error: Optional[str] = None,
) -> dict[str, Any]:
    """Return a non-throwing, setup-ID-aware execution audit.

    Every corruption is a named violation.  This lets a scored run close as an
    integrity failure instead of crashing before its immutable report is saved.
    """
    records = ledger.records
    bar_by_timestamp = {
        record.timestamp_utc: record
        for record in records
        if record.event_type == EventType.BAR
    }
    bar_positions = [
        index for index, record in enumerate(records) if record.event_type == EventType.BAR
    ]
    signals = [
        (index, record)
        for index, record in enumerate(records)
        if record.event_type == EventType.SIGNAL_DECISION
        and record.payload.get("reason") in ENTRY_REASONS
        and record.payload.get("decision") == "accepted"
    ]
    rejected_signals = [
        (index, record)
        for index, record in enumerate(records)
        if record.event_type == EventType.SIGNAL_DECISION
        and record.payload.get("decision") == "rejected"
        and (
            record.payload.get("branch") == OVERNIGHT_DISPLACEMENT_REVERSAL_REASON
            or str(record.payload.get("setup_id", "")).startswith(SETUP_PREFIX)
        )
    ]
    intents = [
        (index, record)
        for index, record in enumerate(records)
        if record.event_type == EventType.ORDER_INTENT
        and record.payload.get("reason") in ENTRY_REASONS
    ]
    risk_vetoes = [
        record
        for record in records
        if record.event_type == EventType.RISK_VETO
        and record.payload.get("reason") in ENTRY_REASONS
    ]
    transitions = [
        record
        for record in records
        if record.event_type == EventType.STATE_TRANSITION
        and record.payload.get("transition") in _TERMINAL_ENTRY_TRANSITIONS
    ]
    entry_fills = [
        (index, record)
        for index, record in enumerate(records)
        if record.event_type == EventType.FILL and record.payload.get("reason") in ENTRY_REASONS
    ]
    exit_fills = [
        record
        for record in records
        if record.event_type == EventType.FILL and record.payload.get("reason") not in ENTRY_REASONS
    ]
    stop_updates = [
        record
        for record in records
        if record.event_type == EventType.STOP_UPDATE
        and record.payload.get("reason") == "initial_stop"
        and record.payload.get("applied") is True
    ]
    exit_decisions = [
        (index, record)
        for index, record in enumerate(records)
        if record.event_type == EventType.EXIT
    ]
    trade_records = [record for record in records if record.event_type == EventType.TRADE_CLOSED]
    diagnostic_fills = [
        event for event in diagnostic_events if _field(event, "event") == "filled"
    ]
    diagnostic_confirmations = [
        event for event in diagnostic_events if _field(event, "event") == "entry_confirmed"
    ]
    diagnostic_rejections = [
        event for event in diagnostic_events if _field(event, "event") == "entry_rejected"
    ]
    violations: list[str] = []

    def add(condition: bool, name: str) -> None:
        if condition:
            violations.append(name)

    add(attribution_error is not None, f"attribution_error:{attribution_error}")
    add(bool(risk_vetoes), "accepted_entry_risk_veto")
    add(
        any(item.payload.get("transition") == "entry_invalidated_at_fill" for item in transitions),
        "entry_invalidated_at_fill",
    )
    add(bool(transitions), "accepted_intent_terminal_without_fill")
    add(len(diagnostic_confirmations) != len(signals), "confirmation_signal_count_mismatch")
    add(len(diagnostic_rejections) != len(rejected_signals), "rejected_signal_diagnostic_count_mismatch")
    add(len(signals) != len(intents) + len(risk_vetoes), "signal_intent_outcome_count_mismatch")
    add(len(intents) != len(entry_fills) + len(transitions), "intent_terminal_outcome_count_mismatch")
    add(len(entry_fills) != len(trades), "entry_fill_trade_count_mismatch")
    add(len(diagnostic_fills) != len(entry_fills), "diagnostic_fill_count_mismatch")
    add(len(stop_updates) != len(entry_fills), "initial_stop_update_count_mismatch")
    add(len(exit_fills) != len(trades), "exit_fill_trade_count_mismatch")
    add(len(trade_records) != len(trades), "trade_ledger_count_mismatch")

    for ordinal, (_, rejected) in enumerate(rejected_signals, start=1):
        setup = _setup_id(rejected.payload)
        add(setup is None, f"rejected_signal_missing_setup_id_{ordinal}")
        if setup is None:
            continue
        expected_setup = _expected_setup_id(rejected)
        add(setup != expected_setup, f"rejected_signal_noncanonical_setup_id:{setup}")
        matches = [
            event
            for event in diagnostic_rejections
            if _diagnostic_setup_id(event) == setup
            and _field(event, "timestamp_utc") == rejected.timestamp_utc
            and str(_enum_value(_field(event, "side"))) == str(rejected.payload.get("side"))
            and str(_metadata(event).get("reason")) == str(rejected.payload.get("reason"))
        ]
        add(len(matches) != 1, f"rejected_signal_diagnostic_mismatch:{setup}")
        add(setup in {_setup_id(item[1].payload) for item in intents}, f"rejected_signal_emitted_intent:{setup}")

    signal_by_setup: dict[str, tuple[int, EventRecord]] = {}
    intent_by_setup: dict[str, tuple[int, EventRecord]] = {}
    setup_by_entry_key: dict[tuple[str, str, str], tuple[str, EventRecord]] = {}
    for kind, items, target in (
        ("signal", signals, signal_by_setup),
        ("intent", intents, intent_by_setup),
    ):
        for ordinal, item in enumerate(items, start=1):
            setup = _setup_id(item[1].payload)
            add(setup is None, f"{kind}_missing_setup_id_{ordinal}")
            if setup is None:
                continue
            add(setup in target, f"duplicate_{kind}_setup_id:{setup}")
            target[setup] = item

    add(set(signal_by_setup) != set(intent_by_setup), "signal_intent_setup_id_set_mismatch")

    for setup in sorted(set(signal_by_setup) & set(intent_by_setup)):
        _, signal = signal_by_setup[setup]
        intent_index, intent = intent_by_setup[setup]
        expected_setup = _expected_setup_id(signal)
        add(setup != expected_setup, f"noncanonical_setup_id:{setup}")
        expected_side = "buy" if signal.payload.get("side") == "long" else "sell"
        signal_metadata = {
            key: value
            for key, value in signal.payload.items()
            if key not in {"symbol", "decision", "side", "reason"}
        }
        intent_metadata = {
            key: value
            for key, value in intent.payload.items()
            if key not in {"symbol", "side", "quantity", "order_type", "reason"}
        }
        add(
            signal_metadata != intent_metadata,
            f"signal_intent_full_metadata_mismatch:{setup}",
        )
        for field_name in (
            "symbol",
            "reason",
            "signal_price",
            "stop_price",
            "target_price",
            "setup_id",
            "branch",
            "gap_direction",
            "prior_rth_close",
            "rth_open",
            "dtr20",
        ):
            add(
                signal.payload.get(field_name) != intent.payload.get(field_name),
                f"signal_intent_{field_name}_mismatch:{setup}",
            )
        add(signal.timestamp_utc != intent.timestamp_utc, f"signal_intent_timestamp_mismatch:{setup}")
        add(intent.payload.get("side") != expected_side, f"signal_intent_side_mismatch:{setup}")
        add(intent.payload.get("quantity") != 1, f"intent_quantity_not_one:{setup}")
        add(intent.payload.get("order_type") != "market_entry", f"intent_order_type_mismatch:{setup}")
        signal_price = intent.payload.get("signal_price")
        stop_at_signal = intent.payload.get("stop_price")
        target_at_signal = intent.payload.get("target_price")
        bracket_values_finite = all(
            _finite(value) for value in (signal_price, stop_at_signal, target_at_signal)
        )
        add(not bracket_values_finite, f"nonfinite_signal_bracket:{setup}")
        if bracket_values_finite:
            signal_value = float(signal_price)
            stop_value = float(stop_at_signal)
            target_value = float(target_at_signal)
            for field_name, value in (
                ("signal", signal_value),
                ("stop", stop_value),
                ("target", target_value),
            ):
                add(
                    not math.isclose(value / tick_size, round(value / tick_size), abs_tol=1e-9),
                    f"{field_name}_not_tick_aligned:{setup}",
                )
            if signal.payload.get("side") == "long":
                add(not stop_value < signal_value, f"stop_not_strictly_protective_at_signal:{setup}")
                add(not signal_value < target_value, f"target_not_strictly_ahead_at_signal:{setup}")
            else:
                add(not signal_value < stop_value, f"stop_not_strictly_protective_at_signal:{setup}")
                add(not target_value < signal_value, f"target_not_strictly_ahead_at_signal:{setup}")

        later_fills = [item for item in entry_fills if item[0] > intent_index]
        next_intent_index = min(
            (item[0] for key, item in intent_by_setup.items() if key != setup and item[0] > intent_index),
            default=len(records),
        )
        matching_fills = [item for item in later_fills if item[0] < next_intent_index]
        add(len(matching_fills) != 1, f"intent_fill_count_mismatch:{setup}")
        if not matching_fills:
            continue
        fill_index, fill = matching_fills[0]
        fill_trade_side = "long" if fill.payload.get("side") == "buy" else "short"
        setup_by_entry_key[
            (fill.timestamp_utc, str(fill.payload.get("symbol")), fill_trade_side)
        ] = (setup, intent)
        first_later_bar = bisect_right(bar_positions, intent_index)
        expected_bar_position = first_later_bar + expected_entry_delay_bars
        expected_timestamp = (
            records[bar_positions[expected_bar_position]].timestamp_utc
            if expected_bar_position < len(bar_positions)
            else None
        )
        add(fill.timestamp_utc != expected_timestamp, f"entry_fill_timing_mismatch:{setup}")
        expected_elapsed_minutes = expected_entry_delay_bars + 1
        elapsed_minutes = (
            _parse_utc(fill.timestamp_utc) - _parse_utc(intent.timestamp_utc)
        ).total_seconds() / 60.0
        add(
            elapsed_minutes != expected_elapsed_minutes,
            f"entry_fill_elapsed_minute_mismatch:{setup}",
        )
        add(
            bisect_left(bar_positions, fill_index) - first_later_bar
            != expected_entry_delay_bars + 1,
            f"entry_fill_intervening_bar_count_mismatch:{setup}",
        )
        for field_name in ("symbol", "side", "quantity", "reason"):
            add(
                fill.payload.get(field_name) != intent.payload.get(field_name),
                f"intent_fill_{field_name}_mismatch:{setup}",
            )
        if fill.payload.get("setup_id") is not None:
            add(str(fill.payload["setup_id"]) != setup, f"intent_fill_setup_id_mismatch:{setup}")
        fill_price = fill.payload.get("price")
        stop = intent.payload.get("stop_price")
        target_price = intent.payload.get("target_price")
        bracket_finite = all(_finite(value) for value in (fill_price, stop, target_price))
        add(not bracket_finite, f"nonfinite_fill_bracket:{setup}")
        if bracket_finite:
            fill_value = float(fill_price)
            stop_value = float(stop)
            target_value = float(target_price)
            if signal.payload.get("side") == "long":
                add(not stop_value < fill_value, f"stop_not_strictly_protective_at_fill:{setup}")
                add(not fill_value < target_value, f"target_not_strictly_ahead_at_fill:{setup}")
            else:
                add(not fill_value < stop_value, f"stop_not_strictly_protective_at_fill:{setup}")
                add(not target_value < fill_value, f"target_not_strictly_ahead_at_fill:{setup}")

        fill_bar = bar_by_timestamp.get(fill.timestamp_utc)
        add(fill_bar is None, f"entry_fill_missing_bar:{setup}")
        if fill_bar is not None:
            raw_open = fill_bar.payload.get("open")
            add(not _finite(raw_open), f"entry_fill_bar_open_nonfinite:{setup}")
            add(
                fill.payload.get("raw_price") != raw_open,
                f"entry_fill_raw_price_bar_open_mismatch:{setup}",
            )
            add(
                fill.payload.get("slippage_points") != entry_slippage_points,
                f"entry_fill_slippage_mismatch:{setup}",
            )
            if _finite(raw_open) and _finite(fill.payload.get("price")):
                entry_direction = 1.0 if intent.payload.get("side") == "buy" else -1.0
                expected_fill_price = float(raw_open) + entry_direction * entry_slippage_points
                add(
                    not math.isclose(float(fill.payload["price"]), expected_fill_price, abs_tol=1e-9),
                    f"entry_fill_price_economics_mismatch:{setup}",
                )

        matching_diagnostics = [
            event
            for event in diagnostic_fills
            if _diagnostic_setup_id(event) == setup
            and _field(event, "timestamp_utc") == fill.timestamp_utc
        ]
        add(len(matching_diagnostics) != 1, f"diagnostic_fill_setup_id_mismatch:{setup}")
        matching_stops = [
            update
            for update in stop_updates
            if update.timestamp_utc == fill.timestamp_utc
            and update.payload.get("symbol") == fill.payload.get("symbol")
            and update.payload.get("stop_price") == stop
        ]
        add(len(matching_stops) != 1, f"initial_stop_update_mismatch:{setup}")

    # Every diagnostic fill must carry a canonical, known setup ID.
    for ordinal, event in enumerate(diagnostic_fills, start=1):
        setup = _diagnostic_setup_id(event)
        add(setup is None, f"diagnostic_fill_missing_setup_id_{ordinal}")
        add(setup is not None and setup not in intent_by_setup, f"diagnostic_fill_unknown_setup_id:{setup}")

    entry_fill_by_key = {
        (
            record.timestamp_utc,
            str(record.payload.get("symbol")),
            "long" if record.payload.get("side") == "buy" else "short",
        ): record
        for _, record in entry_fills
    }
    for index, trade in enumerate(trades, start=1):
        key = (trade.entry_timestamp_utc, trade.symbol, trade.side)
        entry_fill = entry_fill_by_key.get(key)
        add(entry_fill is None, f"trade_entry_fill_mismatch_{index}")
        if entry_fill is not None:
            add(entry_fill.payload.get("quantity") != trade.quantity, f"trade_entry_quantity_mismatch_{index}")
            add(not math.isclose(float(entry_fill.payload.get("price")), trade.entry_price, abs_tol=1e-9), f"trade_entry_price_mismatch_{index}")

        matching_exit_fills = [
            fill
            for fill in exit_fills
            if fill.timestamp_utc == trade.exit_timestamp_utc
            and fill.payload.get("symbol") == trade.symbol
            and fill.payload.get("quantity") == trade.quantity
            and fill.payload.get("price") == trade.exit_price
            and fill.payload.get("reason") == trade.exit_reason
        ]
        add(len(matching_exit_fills) != 1, f"trade_exit_fill_mismatch_{index}")
        expected_exit_side = "sell" if trade.side == "long" else "buy"
        if matching_exit_fills:
            add(matching_exit_fills[0].payload.get("side") != expected_exit_side, f"trade_exit_side_mismatch_{index}")
            exit_fill = matching_exit_fills[0]
            add(
                bool(exit_fill.payload.get("ambiguous", False)) != bool(trade.ambiguous_exit),
                f"trade_exit_ambiguity_mismatch_{index}",
            )
            exit_bar = bar_by_timestamp.get(exit_fill.timestamp_utc)
            add(exit_bar is None, f"exit_fill_missing_bar_{index}")
            add(
                exit_fill.payload.get("slippage_points") != exit_slippage_points,
                f"exit_fill_slippage_mismatch_{index}",
            )
            raw_exit = exit_fill.payload.get("raw_price")
            add(not _finite(raw_exit), f"exit_fill_raw_price_nonfinite_{index}")
            if _finite(raw_exit):
                expected_exit_price = float(raw_exit) - (1.0 if trade.side == "long" else -1.0) * exit_slippage_points
                add(
                    not math.isclose(trade.exit_price, expected_exit_price, abs_tol=1e-9),
                    f"exit_fill_price_economics_mismatch_{index}",
                )
            matching_entry_setup = setup_by_entry_key.get(
                (trade.entry_timestamp_utc, trade.symbol, trade.side)
            )
            if matching_entry_setup is not None:
                setup, intent = matching_entry_setup
                frozen_stop = float(intent.payload["stop_price"])
                frozen_target = float(intent.payload["target_price"])
                reason = trade.exit_reason
                if reason == "stop":
                    add(
                        not _finite(raw_exit) or not math.isclose(float(raw_exit), frozen_stop, abs_tol=1e-9),
                        f"stop_exit_raw_price_mismatch:{setup}",
                    )
                elif reason == "target":
                    add(
                        not _finite(raw_exit) or not math.isclose(float(raw_exit), frozen_target, abs_tol=1e-9),
                        f"target_exit_raw_price_mismatch:{setup}",
                    )
                elif reason == "stop_gap" and exit_bar is not None:
                    add(
                        raw_exit != exit_bar.payload.get("open"),
                        f"stop_gap_raw_price_open_mismatch:{setup}",
                    )
                if exit_bar is not None and reason in ("stop", "target"):
                    high = exit_bar.payload.get("high")
                    low = exit_bar.payload.get("low")
                    if _finite(high) and _finite(low):
                        stop_touched = (
                            float(low) <= frozen_stop
                            if trade.side == "long"
                            else float(high) >= frozen_stop
                        )
                        target_touched = (
                            float(high) >= frozen_target
                            if trade.side == "long"
                            else float(low) <= frozen_target
                        )
                        add(reason == "stop" and not stop_touched, f"stop_not_touched:{setup}")
                        add(reason == "target" and not target_touched, f"target_not_touched:{setup}")
                        add(
                            reason == "target" and stop_touched and target_touched,
                            f"target_selected_despite_same_bar_stop_touch:{setup}",
                        )
                        add(
                            reason == "stop" and target_touched and not bool(exit_fill.payload.get("ambiguous")),
                            f"same_bar_stop_target_not_flagged_ambiguous:{setup}",
                        )
        direction = 1.0 if trade.side == "long" else -1.0
        gross_points = (trade.exit_price - trade.entry_price) * direction
        gross_pnl = gross_points * point_value * trade.quantity
        add(not math.isclose(trade.gross_points, gross_points, abs_tol=1e-9), f"trade_gross_points_mismatch_{index}")
        add(not math.isclose(trade.gross_pnl, gross_pnl, abs_tol=1e-9), f"trade_gross_pnl_mismatch_{index}")
        add(not math.isclose(trade.net_pnl, trade.gross_pnl - trade.commission, abs_tol=1e-9), f"trade_net_pnl_mismatch_{index}")
        add(trade.quantity != 1, f"trade_quantity_not_one_{index}")
        add(
            not _finite(trade.commission)
            or not math.isclose(
                trade.commission,
                commission_per_contract_round_trip * trade.quantity,
                abs_tol=1e-9,
            ),
            f"trade_commission_mismatch_{index}",
        )
        add(not _finite(trade.mfe_points) or trade.mfe_points < 0, f"trade_mfe_invalid_{index}")
        add(not _finite(trade.mae_points) or trade.mae_points < 0, f"trade_mae_invalid_{index}")
        add(_parse_utc(trade.exit_timestamp_utc) < _parse_utc(trade.entry_timestamp_utc), f"trade_negative_holding_time_{index}")
        add(classify_timestamp(trade.entry_timestamp_utc).session_date.isoformat() != trade.session_date, f"trade_session_date_mismatch_{index}")
        matching_trade_records = [record for record in trade_records if record.payload == trade.to_payload()]
        add(len(matching_trade_records) != 1, f"trade_ledger_payload_mismatch_{index}")

    # Strategy-originated exits must fill at the next available bar open.  Engine
    # stop/target/session exits have no preceding EXIT decision by design.
    for ordinal, (decision_index, exit_decision) in enumerate(exit_decisions, start=1):
        later_bar_position = bisect_right(bar_positions, decision_index)
        expected_timestamp = (
            records[bar_positions[later_bar_position]].timestamp_utc
            if later_bar_position < len(bar_positions)
            else None
        )
        matches = [
            fill
            for fill in exit_fills
            if fill.payload.get("reason") == exit_decision.payload.get("reason")
            and fill.payload.get("symbol") == exit_decision.payload.get("symbol")
            and fill.timestamp_utc == expected_timestamp
        ]
        add(len(matches) != 1, f"exit_decision_fill_mismatch_{ordinal}")
        if str(exit_decision.payload.get("reason", "")).endswith("_time_exit"):
            decision_info = classify_timestamp(exit_decision.timestamp_utc)
            add(
                decision_info.minutes_from_midnight_et != 11 * 60 + 59,
                f"time_exit_decision_not_11_59_et_{ordinal}",
            )
            if matches:
                fill_info = classify_timestamp(matches[0].timestamp_utc)
                add(
                    fill_info.minutes_from_midnight_et != 12 * 60,
                    f"time_exit_fill_not_12_00_et_{ordinal}",
                )
                add(
                    (_parse_utc(matches[0].timestamp_utc) - _parse_utc(exit_decision.timestamp_utc)).total_seconds()
                    != 60.0,
                    f"time_exit_elapsed_minute_mismatch_{ordinal}",
                )
                fill_bar = bar_by_timestamp.get(matches[0].timestamp_utc)
                add(
                    fill_bar is None
                    or matches[0].payload.get("raw_price") != fill_bar.payload.get("open"),
                    f"time_exit_raw_price_open_mismatch_{ordinal}",
                )
    for fill in exit_fills:
        if fill.payload.get("reason") in _ENGINE_EXIT_REASONS:
            continue
        matches = [
            decision
            for _, decision in exit_decisions
            if decision.payload.get("reason") == fill.payload.get("reason")
            and decision.payload.get("symbol") == fill.payload.get("symbol")
        ]
        add(len(matches) != 1, f"exit_fill_decision_mismatch:{fill.timestamp_utc}")

    signal_sessions = Counter(
        classify_timestamp(record.timestamp_utc).session_date.isoformat()
        for _, record in signals + rejected_signals
    )
    intent_sessions = Counter(classify_timestamp(record.timestamp_utc).session_date.isoformat() for _, record in intents)
    fill_sessions = Counter(classify_timestamp(record.timestamp_utc).session_date.isoformat() for _, record in entry_fills)
    trade_sessions = Counter(trade.session_date for trade in trades)
    add(any(value > 1 for value in signal_sessions.values()), "more_than_one_entry_attempt_in_session")
    add(any(value > 1 for value in intent_sessions.values()), "more_than_one_order_intent_in_session")
    add(any(value > 1 for value in fill_sessions.values()), "more_than_one_entry_fill_in_session")
    add(any(value > 1 for value in trade_sessions.values()), "more_than_one_closed_trade_in_session")

    # De-duplicate only exact repeats so the report remains concise while every
    # distinct corruption retains its name.
    unique_violations = list(dict.fromkeys(violations))
    return {
        "accepted_signal_count": len(signals),
        "rejected_signal_count": len(rejected_signals),
        "order_intent_count": len(intents),
        "entry_fill_count": len(entry_fills),
        "exit_fill_count": len(exit_fills),
        "trade_count": len(trades),
        "trade_ledger_count": len(trade_records),
        "entry_risk_veto_count": len(risk_vetoes),
        "entry_terminal_transition_count": len(transitions),
        "entry_invalidated_at_fill_count": sum(
            item.payload.get("transition") == "entry_invalidated_at_fill"
            for item in transitions
        ),
        "violations": unique_violations,
        "violation_count": len(unique_violations),
    }


def _gap_bucket(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value < 0.10:
        return "0.05-0.10"
    if value < 0.25:
        return "0.10-0.25"
    if value < 0.50:
        return "0.25-0.50"
    return "0.50-0.75"


def _breadth_bucket(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value < 0.60:
        return "0.50-0.60"
    if value < 0.75:
        return "0.60-0.75"
    if value < 0.90:
        return "0.75-0.90"
    return "0.90-1.00"


def _audit_active_rth_continuity(
    *,
    ledger: EventLedger,
    snapshots: Sequence[Any],
    diagnostic_events: Sequence[Any],
) -> dict[str, Any]:
    """Independently reconstruct contiguous active RTH minutes from BAR events."""
    eligible_sessions = {
        _snapshot_session(snapshot)
        for snapshot in snapshots
        if _classification_eligible(snapshot)
    }
    minutes_by_session: dict[str, list[int]] = {}
    for record in ledger.records:
        if record.event_type != EventType.BAR:
            continue
        info = classify_timestamp(record.timestamp_utc)
        session = info.session_date.isoformat()
        if session in eligible_sessions and info.is_rth and 570 <= info.minutes_from_midnight_et <= 659:
            minutes_by_session.setdefault(session, []).append(info.minutes_from_midnight_et)

    events_by_session: dict[str, list[Any]] = {}
    diagnostic_gap_sessions: set[str] = set()
    for event in diagnostic_events:
        session = str(_field(event, "session_date"))
        if session not in eligible_sessions:
            continue
        events_by_session.setdefault(session, []).append(event)
        reason = str(_metadata(event).get("reason", ""))
        if _field(event, "event") == "entry_cancelled" and (
            reason == "active_rth_minute_gap"
            or ("minute" in reason and "gap" in reason)
            or reason == "missing_active_rth_minute"
        ):
            diagnostic_gap_sessions.add(session)

    details: dict[str, Any] = {}
    reconstructed_gap_sessions: set[str] = set()
    for session in sorted(eligible_sessions):
        terminal_minute = 659
        terminals = [
            event
            for event in events_by_session.get(session, [])
            if _field(event, "event")
            in ("entry_confirmed", "entry_cancelled", "entry_rejected")
        ]
        if terminals:
            first_terminal = min(terminals, key=lambda item: _parse_utc(str(_field(item, "timestamp_utc"))))
            terminal_info = classify_timestamp(str(_field(first_terminal, "timestamp_utc")))
            terminal_minute = min(659, max(570, terminal_info.minutes_from_midnight_et))
        observed_list = minutes_by_session.get(session, [])
        observed = set(observed_list)
        expected = set(range(570, terminal_minute + 1))
        missing = sorted(expected - observed)
        duplicates = sorted(minute for minute, count in Counter(observed_list).items() if count > 1)
        if missing or duplicates:
            reconstructed_gap_sessions.add(session)
        details[session] = {
            "active_start_minute_et": 570,
            "active_end_minute_et": terminal_minute,
            "expected_minute_count": len(expected),
            "observed_unique_minute_count": len(observed & expected),
            "missing_minutes_et": missing,
            "duplicate_minutes_et": duplicates,
        }
    return {
        "eligible_session_count": len(eligible_sessions),
        "reconstructed_gap_sessions": sorted(reconstructed_gap_sessions),
        "diagnostic_gap_sessions": sorted(diagnostic_gap_sessions),
        "diagnostic_missing_for_reconstructed_gap": sorted(
            reconstructed_gap_sessions - diagnostic_gap_sessions
        ),
        "diagnostic_without_reconstructed_gap": sorted(
            diagnostic_gap_sessions - reconstructed_gap_sessions
        ),
        "details": details,
    }


def _audit_state_completeness(
    *, snapshots: Sequence[Any], diagnostic_events: Sequence[Any]
) -> dict[str, Any]:
    eligible = {
        _snapshot_session(snapshot): snapshot
        for snapshot in snapshots
        if _classification_eligible(snapshot)
    }
    events_by_session: dict[str, list[Any]] = {}
    for event in diagnostic_events:
        session = str(_field(event, "session_date"))
        if session in eligible:
            events_by_session.setdefault(session, []).append(event)
    state_rank = {
        "observe_overnight": 0,
        "eligible_gap": 1,
        "wait_extension": 1,
        "wait_rejection": 2,
        "entry_pending": 3,
        "position": 4,
        "done": 5,
    }
    details: dict[str, Any] = {}
    violations: list[str] = []
    for session, snapshot in sorted(eligible.items()):
        classification = _field(snapshot, "classification")
        expected_setup = str(_field(classification, "setup_id"))
        expected_side = str(_enum_value(_field(classification, "side")))
        events = sorted(
            events_by_session.get(session, []),
            key=lambda event: _parse_utc(str(_field(event, "timestamp_utc"))),
        )
        classified = [event for event in events if _field(event, "event") == "classified"]
        if len(classified) != 1:
            violations.append(f"eligible_session_classified_count_mismatch:{session}")
        terminal = [
            event
            for event in events
            if _field(event, "event") in ("entry_cancelled", "entry_rejected", "filled")
        ]
        if len(terminal) != 1:
            violations.append(f"eligible_session_terminal_outcome_count_mismatch:{session}")
        if any(_field(event, "event") == "entry_pending_expired" for event in events):
            violations.append(f"entry_pending_expired:{session}")
        prior_rank = -1
        for ordinal, event in enumerate(events, start=1):
            setup = _diagnostic_setup_id(event)
            if setup != expected_setup:
                violations.append(f"diagnostic_setup_id_mismatch:{session}:{ordinal}")
            side = str(_enum_value(_field(event, "side", expected_side)))
            if side not in (expected_side, "none", "None"):
                violations.append(f"diagnostic_side_mismatch:{session}:{ordinal}")
            branch = _field(event, "branch")
            if branch not in (None, OVERNIGHT_DISPLACEMENT_REVERSAL_REASON):
                violations.append(f"diagnostic_branch_mismatch:{session}:{ordinal}")
            state = str(_enum_value(_field(event, "state", "")))
            rank = state_rank.get(state)
            if rank is None:
                violations.append(f"unknown_diagnostic_state:{session}:{state}")
                continue
            if rank < prior_rank:
                violations.append(f"diagnostic_state_regression:{session}:{ordinal}")
            prior_rank = max(prior_rank, rank)
        details[session] = {
            "event_count": len(events),
            "classified_count": len(classified),
            "terminal_outcome_count": len(terminal),
            "terminal_events": [str(_field(event, "event")) for event in terminal],
        }
    unique = list(dict.fromkeys(violations))
    return {
        "eligible_session_count": len(eligible),
        "violations": unique,
        "violation_count": len(unique),
        "details": details,
    }


def _previous_expected_rth_session(session: date) -> Optional[date]:
    cursor = session - timedelta(days=1)
    for _ in range(10):
        if rth_close_minutes_et(cursor) is not None:
            return cursor
        cursor -= timedelta(days=1)
    return None


def _audit_snapshot_calendar_and_roll(snapshots: Sequence[Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    violations: list[str] = []
    for snapshot in snapshots:
        features = _field(snapshot, "features")
        session_iso = _snapshot_session(snapshot)
        session = date.fromisoformat(session_iso)
        prior = _previous_expected_rth_session(session)
        if prior is None:
            violations.append(f"missing_prior_expected_rth_session:{session_iso}")
            continue
        prior_close_minute = rth_close_minutes_et(prior)
        expected_minutes = (
            int(prior_close_minute) - 570 if prior_close_minute is not None else None
        )
        reported_prior_session = _field(features, "prior_rth_session_date")
        reported_expected = _field(features, "prior_rth_expected_minutes")
        reported_observed = _field(features, "prior_rth_observed_minutes")
        reported_complete = bool(_field(features, "prior_rth_complete", False))
        derived_complete = bool(
            reported_prior_session == prior.isoformat()
            and reported_expected == expected_minutes
            and reported_observed == expected_minutes
            and bool(_field(features, "prior_rth_all_finite", False))
            and _finite(_field(features, "prior_rth_close"))
        )
        if reported_prior_session != prior.isoformat():
            violations.append(f"prior_rth_session_date_mismatch:{session_iso}")
        if reported_expected != expected_minutes:
            violations.append(f"prior_rth_expected_minute_count_mismatch:{session_iso}")
        if reported_complete != derived_complete:
            violations.append(f"prior_rth_complete_flag_mismatch:{session_iso}")
        expected_current_contract = front_contract_for_session(session)
        expected_prior_contract = front_contract_for_session(prior)
        reported_current_contract = _field(features, "current_contract")
        reported_prior_contract = _field(features, "prior_rth_contract")
        if reported_current_contract != expected_current_contract:
            violations.append(f"current_contract_identity_mismatch:{session_iso}")
        if reported_prior_contract != expected_prior_contract:
            violations.append(f"prior_contract_identity_mismatch:{session_iso}")
        expected_roll = expected_current_contract != expected_prior_contract
        reported_roll = bool(_field(features, "roll_transition", False))
        if reported_roll != expected_roll:
            violations.append(f"roll_transition_flag_mismatch:{session_iso}")
        expected_current_rth = rth_close_minutes_et(session) is not None
        if bool(_field(features, "current_rth_session", False)) != expected_current_rth:
            violations.append(f"current_rth_calendar_flag_mismatch:{session_iso}")

        overnight_bar_count = _field(features, "overnight_bar_count")
        overnight_first = _field(features, "overnight_first_offset_minutes")
        overnight_last = _field(features, "overnight_last_offset_minutes")
        overnight_max_gap = _field(features, "overnight_max_gap_minutes")
        computed_overnight_complete = bool(
            isinstance(overnight_bar_count, int)
            and overnight_bar_count >= 2
            and isinstance(overnight_first, int)
            and overnight_first <= 5
            and isinstance(overnight_last, int)
            and overnight_last >= 924
            and isinstance(overnight_max_gap, int)
            and overnight_max_gap <= 15
            and bool(_field(features, "overnight_all_finite", False))
            and _finite(_field(features, "overnight_total_volume"))
            and float(_field(features, "overnight_total_volume")) > 0
        )
        if bool(_field(features, "complete_overnight", False)) != computed_overnight_complete:
            violations.append(f"overnight_complete_flag_mismatch:{session_iso}")
        overnight_high = _field(features, "overnight_high")
        overnight_low = _field(features, "overnight_low")
        overnight_range = _field(features, "overnight_range")
        if all(_finite(value) for value in (overnight_high, overnight_low, overnight_range)):
            if not math.isclose(
                float(overnight_range),
                float(overnight_high) - float(overnight_low),
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                violations.append(f"overnight_range_geometry_mismatch:{session_iso}")

        dtr_dates_raw = tuple(_field(features, "dtr_session_dates", ()))
        dtr_values_raw = tuple(_field(features, "dtr_values", ()))
        dtr_valid = len(dtr_dates_raw) == 20 and len(dtr_values_raw) == 20
        try:
            dtr_dates = tuple(date.fromisoformat(str(item)) for item in dtr_dates_raw)
        except (TypeError, ValueError):
            dtr_dates = ()
            dtr_valid = False
        if dtr_valid:
            dtr_valid = bool(
                tuple(sorted(dtr_dates)) == dtr_dates
                and len(set(dtr_dates)) == 20
                and all(day < session and rth_close_minutes_et(day) is not None for day in dtr_dates)
                and all(_finite(value) and float(value) > 0 for value in dtr_values_raw)
                and _finite(_field(features, "dtr20"))
                and math.isclose(
                    statistics.median(float(value) for value in dtr_values_raw),
                    float(_field(features, "dtr20")),
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )
        if dtr_valid:
            for dtr_day in dtr_dates:
                dtr_prior = _previous_expected_rth_session(dtr_day)
                if dtr_prior is None or front_contract_for_session(dtr_day) != front_contract_for_session(dtr_prior):
                    dtr_valid = False
                    break
        if not dtr_valid:
            violations.append(f"dtr_provenance_mismatch:{session_iso}")

        prior_close = _field(features, "prior_rth_close")
        rth_open = _field(features, "rth_open")
        dtr20 = _field(features, "dtr20")
        if all(_finite(value) for value in (prior_close, rth_open, dtr20)) and float(dtr20) > 0:
            expected_signed_gap = float(rth_open) - float(prior_close)
            expected_direction = "up" if expected_signed_gap > 0 else "down" if expected_signed_gap < 0 else None
            expected_gap_dtr = abs(expected_signed_gap) / float(dtr20)
            if not _finite(_field(features, "gap_signed_points")) or not math.isclose(
                float(_field(features, "gap_signed_points")), expected_signed_gap, rel_tol=1e-12, abs_tol=1e-12
            ):
                violations.append(f"gap_signed_points_mismatch:{session_iso}")
            if _field(features, "gap_direction") != expected_direction:
                violations.append(f"gap_direction_mismatch:{session_iso}")
            if not _finite(_field(features, "gap_dtr")) or not math.isclose(
                float(_field(features, "gap_dtr")), expected_gap_dtr, rel_tol=1e-12, abs_tol=1e-12
            ):
                violations.append(f"gap_dtr_mismatch:{session_iso}")
            above = _field(features, "breadth_above_prior_close")
            below = _field(features, "breadth_below_prior_close")
            if not (
                _finite(above)
                and _finite(below)
                and 0 <= float(above) <= 1
                and 0 <= float(below) <= 1
                and float(above) + float(below) <= 1 + 1e-12
            ):
                violations.append(f"breadth_geometry_mismatch:{session_iso}")
            else:
                expected_breadth = float(above) if expected_direction == "up" else float(below) if expected_direction == "down" else None
                if expected_breadth is None or not _finite(_field(features, "displacement_breadth")) or not math.isclose(
                    float(_field(features, "displacement_breadth")), expected_breadth, rel_tol=1e-12, abs_tol=1e-12
                ):
                    violations.append(f"aligned_displacement_breadth_mismatch:{session_iso}")
        classification = _field(snapshot, "classification")
        side = str(_enum_value(_field(classification, "side", "none")))
        if side in ("long", "short"):
            expected_setup = f"{SETUP_PREFIX}{session_iso}:{side}"
            if str(_field(classification, "setup_id")) != expected_setup:
                violations.append(f"snapshot_setup_id_noncanonical:{session_iso}")
        details[session_iso] = {
            "prior_rth_session_date": prior.isoformat(),
            "expected_prior_rth_minutes": expected_minutes,
            "reported_prior_rth_expected_minutes": reported_expected,
            "reported_prior_rth_observed_minutes": reported_observed,
            "reported_prior_rth_complete": reported_complete,
            "derived_prior_rth_complete": derived_complete,
            "expected_prior_contract": expected_prior_contract,
            "expected_current_contract": expected_current_contract,
            "reported_prior_contract": reported_prior_contract,
            "reported_current_contract": reported_current_contract,
            "expected_roll_transition": expected_roll,
            "reported_roll_transition": reported_roll,
            "computed_overnight_complete": computed_overnight_complete,
            "reported_overnight_complete": bool(_field(features, "complete_overnight", False)),
            "dtr_provenance_valid": dtr_valid,
        }
    unique = list(dict.fromkeys(violations))
    return {
        "violations": unique,
        "violation_count": len(unique),
        "details": details,
    }


def _audit_dtr_against_bar_ledger(
    *, ledger: EventLedger, snapshots: Sequence[Any]
) -> dict[str, Any]:
    """Rebuild every causal DTR source from complete RTH BAR records once."""
    sessions: dict[str, dict[str, Any]] = {}
    for record in ledger.records:
        if record.event_type != EventType.BAR:
            continue
        info = classify_timestamp(record.timestamp_utc)
        if not info.is_rth:
            continue
        day = info.session_date.isoformat()
        bucket = sessions.setdefault(
            day,
            {
                "minutes": [],
                "all_finite": True,
                "high": None,
                "low": None,
                "open_by_minute": {},
                "close_by_minute": {},
                "timestamp_by_minute": {},
            },
        )
        bucket["minutes"].append(info.minutes_from_midnight_et)
        values = tuple(record.payload.get(name) for name in ("open", "high", "low", "close", "volume"))
        if not all(_finite(value) for value in values):
            bucket["all_finite"] = False
            continue
        high = float(record.payload["high"])
        low = float(record.payload["low"])
        bucket["high"] = high if bucket["high"] is None else max(bucket["high"], high)
        bucket["low"] = low if bucket["low"] is None else min(bucket["low"], low)
        bucket["open_by_minute"][info.minutes_from_midnight_et] = float(record.payload["open"])
        bucket["close_by_minute"][info.minutes_from_midnight_et] = float(record.payload["close"])
        bucket["timestamp_by_minute"][info.minutes_from_midnight_et] = record.timestamp_utc

    complete: dict[str, dict[str, float]] = {}
    for day, bucket in sessions.items():
        close_minute = rth_close_minutes_et(date.fromisoformat(day))
        if close_minute is None:
            continue
        expected = list(range(570, int(close_minute)))
        if (
            bucket["minutes"] == expected
            and bucket["all_finite"]
            and bucket["high"] is not None
            and bucket["low"] is not None
            and 570 in bucket["open_by_minute"]
            and int(close_minute) - 1 in bucket["close_by_minute"]
        ):
            complete[day] = {
                "high": float(bucket["high"]),
                "low": float(bucket["low"]),
                "open": float(bucket["open_by_minute"][570]),
                "close": float(bucket["close_by_minute"][int(close_minute) - 1]),
                "open_timestamp_utc": str(bucket["timestamp_by_minute"][570]),
            }

    reconstructed: list[tuple[str, float]] = []
    for day in sorted(complete):
        current = date.fromisoformat(day)
        prior = _previous_expected_rth_session(current)
        prior_iso = prior.isoformat() if prior is not None else None
        if prior_iso is None or prior_iso not in complete:
            continue
        if front_contract_for_session(current) != front_contract_for_session(prior):
            continue
        current_values = complete[day]
        prior_close = complete[prior_iso]["close"]
        true_range = max(
            current_values["high"] - current_values["low"],
            abs(current_values["high"] - prior_close),
            abs(current_values["low"] - prior_close),
        )
        if _finite(true_range) and true_range > 0:
            reconstructed.append((day, true_range))

    violations: list[str] = []
    details: dict[str, Any] = {}
    for snapshot in snapshots:
        session = _snapshot_session(snapshot)
        features = _field(snapshot, "features")
        expected_sources = [item for item in reconstructed if item[0] < session][-20:]
        reported_dates = tuple(str(item) for item in _field(features, "dtr_session_dates", ()))
        reported_values = tuple(float(item) for item in _field(features, "dtr_values", ()) if _finite(item))
        expected_dates = tuple(item[0] for item in expected_sources)
        expected_values = tuple(item[1] for item in expected_sources)
        dates_match = reported_dates == expected_dates
        values_match = len(reported_values) == len(expected_values) and all(
            math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-9)
            for left, right in zip(reported_values, expected_values)
        )
        expected_dtr = statistics.median(expected_values) if len(expected_values) == 20 else None
        reported_dtr = _field(features, "dtr20")
        dtr_match = (
            reported_dtr is None
            if expected_dtr is None
            else _finite(reported_dtr)
            and math.isclose(float(reported_dtr), expected_dtr, rel_tol=1e-12, abs_tol=1e-9)
        )
        if not dates_match:
            violations.append(f"dtr_source_dates_bar_ledger_mismatch:{session}")
        if not values_match:
            violations.append(f"dtr_source_values_bar_ledger_mismatch:{session}")
        if not dtr_match:
            violations.append(f"dtr20_bar_ledger_mismatch:{session}")
        reported_prior_session = _field(features, "prior_rth_session_date")
        expected_prior_close = (
            complete.get(str(reported_prior_session), {}).get("close")
            if reported_prior_session is not None
            else None
        )
        prior_close_match = (
            expected_prior_close is not None
            and _finite(_field(features, "prior_rth_close"))
            and math.isclose(
                float(_field(features, "prior_rth_close")),
                float(expected_prior_close),
                rel_tol=1e-12,
                abs_tol=1e-9,
            )
        )
        rth_open_match = (
            session in complete
            and _finite(_field(features, "rth_open"))
            and math.isclose(
                float(_field(features, "rth_open")),
                float(complete[session]["open"]),
                rel_tol=1e-12,
                abs_tol=1e-9,
            )
        )
        if not prior_close_match:
            violations.append(f"prior_rth_close_bar_ledger_mismatch:{session}")
        if not rth_open_match:
            violations.append(f"rth_open_bar_ledger_mismatch:{session}")
        classification_timestamp_match = bool(
            session in complete
            and _field(features, "classification_timestamp_utc")
            == complete[session]["open_timestamp_utc"]
        )
        if not classification_timestamp_match:
            violations.append(f"classification_timestamp_not_completed_09_30_bar:{session}")
        details[session] = {
            "expected_source_dates": list(expected_dates),
            "reported_source_dates": list(reported_dates),
            "expected_source_values": list(expected_values),
            "reported_source_values": list(reported_values),
            "expected_dtr20": expected_dtr,
            "reported_dtr20": reported_dtr,
            "expected_prior_rth_close": expected_prior_close,
            "reported_prior_rth_close": _field(features, "prior_rth_close"),
            "expected_rth_open": complete.get(session, {}).get("open"),
            "reported_rth_open": _field(features, "rth_open"),
            "expected_classification_timestamp_utc": complete.get(session, {}).get("open_timestamp_utc"),
            "reported_classification_timestamp_utc": _field(features, "classification_timestamp_utc"),
            "passed": dates_match and values_match and dtr_match and prior_close_match and rth_open_match and classification_timestamp_match,
        }
    unique = list(dict.fromkeys(violations))
    return {
        "complete_rth_sessions_reconstructed": len(complete),
        "valid_nonroll_true_ranges_reconstructed": len(reconstructed),
        "violations": unique,
        "violation_count": len(unique),
        "details": details,
    }


def _audit_overnight_against_bar_ledger(
    *, ledger: EventLedger, snapshots: Sequence[Any]
) -> dict[str, Any]:
    snapshot_by_session = {_snapshot_session(snapshot): snapshot for snapshot in snapshots}
    accumulators: dict[str, dict[str, Any]] = {}
    for record in ledger.records:
        if record.event_type != EventType.BAR:
            continue
        info = classify_timestamp(record.timestamp_utc)
        session = info.session_date.isoformat()
        if session not in snapshot_by_session:
            continue
        minute = info.minutes_from_midnight_et
        offset = minute - 1080 if minute >= 1080 else 360 + minute if minute <= 569 else None
        if offset is None:
            continue
        bucket = accumulators.setdefault(
            session,
            {
                "offsets": [],
                "all_finite": True,
                "total_volume": 0.0,
                "pv": 0.0,
                "high": None,
                "low": None,
                "close": None,
                "closes": [],
            },
        )
        bucket["offsets"].append(offset)
        values = tuple(record.payload.get(name) for name in ("open", "high", "low", "close", "volume"))
        if not all(_finite(value) for value in values):
            bucket["all_finite"] = False
            continue
        high = float(record.payload["high"])
        low = float(record.payload["low"])
        close = float(record.payload["close"])
        volume = float(record.payload["volume"])
        bucket["total_volume"] += volume
        bucket["pv"] += ((high + low + close) / 3.0) * volume
        bucket["high"] = high if bucket["high"] is None else max(bucket["high"], high)
        bucket["low"] = low if bucket["low"] is None else min(bucket["low"], low)
        bucket["close"] = close
        bucket["closes"].append(close)

    violations: list[str] = []
    details: dict[str, Any] = {}
    for session, snapshot in sorted(snapshot_by_session.items()):
        features = _field(snapshot, "features")
        bucket = accumulators.get(
            session,
            {
                "offsets": [],
                "all_finite": True,
                "total_volume": 0.0,
                "pv": 0.0,
                "high": None,
                "low": None,
                "close": None,
                "closes": [],
            },
        )
        offsets = list(bucket["offsets"])
        first = offsets[0] if offsets else None
        last = offsets[-1] if offsets else None
        max_gap = max((right - left for left, right in zip(offsets, offsets[1:])), default=None)
        ordered_unique = all(right > left for left, right in zip(offsets, offsets[1:]))
        complete = bool(
            len(offsets) >= 2
            and first is not None
            and first <= 5
            and last is not None
            and last >= 924
            and max_gap is not None
            and max_gap <= 15
            and ordered_unique
            and bucket["all_finite"]
            and _finite(bucket["total_volume"])
            and bucket["total_volume"] > 0
            and len(bucket["closes"]) == len(offsets)
        )
        expected_vwap = bucket["pv"] / bucket["total_volume"] if complete else None
        expected_range = (
            float(bucket["high"]) - float(bucket["low"])
            if bucket["high"] is not None and bucket["low"] is not None
            else None
        )
        prior_close = _field(features, "prior_rth_close")
        above = below = aligned = None
        if _finite(prior_close) and offsets:
            above = sum(value > float(prior_close) for value in bucket["closes"]) / len(offsets)
            below = sum(value < float(prior_close) for value in bucket["closes"]) / len(offsets)
            direction = _field(features, "gap_direction")
            aligned = above if direction == "up" else below if direction == "down" else None

        exact_fields = {
            "overnight_bar_count": len(offsets),
            "overnight_first_offset_minutes": first,
            "overnight_last_offset_minutes": last,
            "overnight_max_gap_minutes": max_gap,
            "overnight_all_finite": bool(bucket["all_finite"]),
            "complete_overnight": complete,
        }
        for field_name, expected_value in exact_fields.items():
            if _field(features, field_name) != expected_value:
                violations.append(f"{field_name}_bar_ledger_mismatch:{session}")
        numeric_fields = {
            "overnight_total_volume": bucket["total_volume"],
            "overnight_high": bucket["high"],
            "overnight_low": bucket["low"],
            "overnight_close": bucket["close"],
            "overnight_range": expected_range,
            "overnight_vwap": expected_vwap,
            "breadth_above_prior_close": above,
            "breadth_below_prior_close": below,
            "displacement_breadth": aligned,
        }
        for field_name, expected_value in numeric_fields.items():
            reported = _field(features, field_name)
            matches = (
                reported is None and expected_value is None
                or _finite(reported)
                and _finite(expected_value)
                and math.isclose(
                    float(reported),
                    float(expected_value),
                    rel_tol=1e-12,
                    abs_tol=1e-9,
                )
            )
            if not matches:
                violations.append(f"{field_name}_bar_ledger_mismatch:{session}")
        details[session] = {
            "expected": {**exact_fields, **numeric_fields},
            "reported": {
                name: _field(features, name)
                for name in (*exact_fields.keys(), *numeric_fields.keys())
            },
            "ordered_unique_offsets": ordered_unique,
        }
    unique = list(dict.fromkeys(violations))
    return {
        "violations": unique,
        "violation_count": len(unique),
        "details": details,
    }


def evaluate_odr_t1_primary_gates(report: dict[str, Any]) -> dict[str, Any]:
    overall = report["overall"]["survivability"]
    sides = report["by_side"]
    daily = report["daily"]
    weekly = report["weekly"]
    bootstrap = report["bootstrap"]
    folds = report["complete_half_years"]
    positive_folds = sum(fold["net_pnl_including_zero_days"] > 0 for fold in folds.values())
    required_positive = math.ceil(0.70 * len(folds)) if folds else 1
    final_fold = folds[sorted(folds)[-1]] if folds else None
    p95_drawdown = float(bootstrap["max_drawdown_p95_adverse"])
    annualized_net = float(report["risk_efficiency"]["observed_annualized_net_pnl"])
    annualized_to_p95 = annualized_net / abs(p95_drawdown) if p95_drawdown < 0 else None
    annualized_to_p95_pass = bool(
        (annualized_to_p95 is not None and annualized_to_p95 >= 1.0)
        or (p95_drawdown == 0 and annualized_net > 0)
    )
    traded_years = {row["session_date"][:4] for row in report["fill_relative_trades"]}
    checks = {
        "deterministic_replay_verified": report["deterministic_replay"]["verified"],
        "causal_warmup_completed_within_25_expected_sessions": (
            report["score_window"]["causal_warmup_ready_ordinal"] is not None
            and report["score_window"]["causal_warmup_ready_ordinal"] <= 25
        ),
        "zero_missing_expected_cme_sessions": report["score_window"]["missing_expected_session_count"] == 0,
        "zero_unexpected_closed_session_snapshots": report["score_window"]["unexpected_snapshot_session_count"] == 0,
        "zero_active_rth_minute_gap_sessions": report["score_window"]["active_rth_minute_gap_session_count"] == 0,
        "every_score_calendar_session_in_daily_series": daily["trading_days"] == report["score_window"]["expected_score_sessions_after_warmup"],
        "one_frozen_mechanism_only": report["mechanism_counts"] == {OVERNIGHT_DISPLACEMENT_REVERSAL_REASON: overall["trade_count"]},
        "at_least_300_trades": overall["trade_count"] >= 300,
        "trades_span_at_least_three_calendar_years": len(traded_years) >= 3,
        "at_least_75_long_and_short_trades": all(
            sides.get(side, {}).get("survivability", {}).get("trade_count", 0) >= 75
            for side in ("long", "short")
        ),
        "positive_net_pnl": overall["net_pnl"] > 0,
        "positive_expectancy": overall["expectancy_per_trade"] > 0,
        "profit_factor_at_least_1_25": _pf_pass(report["overall"], 1.25),
        "daily_sharpe_at_least_1_25": daily["sharpe_annualized"] >= 1.25,
        "average_weekly_r_at_least_0_50": weekly["realized_net_r"]["mean"] is not None and weekly["realized_net_r"]["mean"] >= 0.50,
        "average_weekly_dollars_positive": weekly["net_pnl"]["mean"] is not None and weekly["net_pnl"]["mean"] > 0,
        "bootstrap_nonpositive_below_5pct": bootstrap["probability_total_net_nonpositive"] < 0.05,
        "annualized_net_to_p95_drawdown_at_least_1": annualized_to_p95_pass,
        "complete_half_year_coverage": len(folds) >= 6 and all(fold["eligible_sessions"] >= 100 for fold in folds.values()),
        "at_least_70pct_complete_half_years_positive": len(folds) > 0 and positive_folds >= required_positive,
        "final_chronological_half_year_positive": final_fold is not None and final_fold["net_pnl_including_zero_days"] > 0,
        "long_and_short_each_positive_pf_at_least_1": all(
            side in sides
            and sides[side]["survivability"]["net_pnl"] > 0
            and _pf_pass(sides[side], 1.0)
            for side in ("long", "short")
        ),
        "positive_without_top_five_trades": overall["pnl_without_top_5_trades"] > 0,
        "positive_without_top_five_days": daily["pnl_without_top_5_days"] > 0,
        "top_five_day_share_no_more_than_35pct": daily["top_5_day_share"] is not None and daily["top_5_day_share"] <= 0.35,
        "bootstrap_p99_drawdown_disclosed": bootstrap["max_drawdown_p99_adverse"] is not None and bootstrap["draws"] >= 20_000,
        "zero_reconciliation_violations": report["execution_diagnostics"]["reconciliation_violation_count"] == 0,
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "positive_complete_half_years": positive_folds,
        "required_positive_complete_half_years": required_positive,
        "annualized_net_to_p95_drawdown": annualized_to_p95,
        "scope": "T1 normal-cost primary only; neighborhood, latency, doubled-cost, capital-policy, and prospective gates remain separate",
    }


def build_overnight_displacement_reversal_report(
    *,
    trades: Sequence[Trade],
    ledger: EventLedger,
    snapshots: Sequence[Any],
    diagnostic_events: Sequence[Any],
    point_value: float,
    score_start_session: str,
    score_end_session_exclusive: str,
    candidate_family_trial_budget: int = 9,
    expected_entry_delay_bars: int = 0,
    commission_per_contract_round_trip: float = 10.0,
    entry_slippage_points: float = 0.75,
    exit_slippage_points: float = 0.75,
    tick_size: float = 0.25,
    allocated_capital: Optional[float] = None,
    hard_loss_limit: Optional[float] = None,
    comparison_daily_pnl: Optional[dict[str, float]] = None,
    comparison_name: Optional[str] = None,
    comparison_provenance: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if (allocated_capital is None) != (hard_loss_limit is None):
        raise ValueError("allocated_capital and hard_loss_limit must be supplied together")
    if allocated_capital is not None and (not _finite(allocated_capital) or allocated_capital <= 0):
        raise ValueError("allocated_capital must be finite and positive")
    if hard_loss_limit is not None and (not _finite(hard_loss_limit) or hard_loss_limit <= 0):
        raise ValueError("hard_loss_limit must be finite and positive")
    if not _finite(point_value) or point_value <= 0:
        raise ValueError("point_value must be finite and positive")
    for name, value, strictly_positive in (
        ("commission_per_contract_round_trip", commission_per_contract_round_trip, False),
        ("entry_slippage_points", entry_slippage_points, False),
        ("exit_slippage_points", exit_slippage_points, False),
        ("tick_size", tick_size, True),
    ):
        if not _finite(value) or value < 0 or (strictly_positive and value <= 0):
            raise ValueError(f"{name} must be finite and {'positive' if strictly_positive else 'nonnegative'}")

    requested_trades = [
        trade for trade in trades if score_start_session <= trade.session_date < score_end_session_exclusive
    ]
    scored_snapshots = [
        snapshot
        for snapshot in snapshots
        if score_start_session <= _snapshot_session(snapshot) < score_end_session_exclusive
    ]
    snapshot_by_session: dict[str, Any] = {}
    for snapshot in scored_snapshots:
        session = _snapshot_session(snapshot)
        if session in snapshot_by_session:
            raise ValueError(f"duplicate session snapshot: {session}")
        snapshot_by_session[session] = snapshot

    expected = _expected_cme_sessions(score_start_session, score_end_session_exclusive)
    expected_set = set(expected)
    missing = sorted(expected_set - set(snapshot_by_session))
    unexpected = sorted(set(snapshot_by_session) - expected_set)
    warmup_ready = [day for day in expected if day in snapshot_by_session and _warmup_ready(snapshot_by_session[day])]
    effective_start = warmup_ready[0] if warmup_ready else None
    warmup_ordinal = expected.index(effective_start) + 1 if effective_start is not None else None
    session_dates = [day for day in expected if effective_start is not None and day >= effective_start]
    score_session_set = set(session_dates)
    scored_trades = [trade for trade in requested_trades if trade.session_date in score_session_set]
    pre_warmup_or_out_of_score_trades = [
        trade for trade in requested_trades if trade.session_date not in score_session_set
    ]
    score_ledger = EventLedger()
    score_ledger.records = [
        record
        for record in ledger.records
        if classify_timestamp(record.timestamp_utc).session_date.isoformat()
        in score_session_set
    ]
    score_snapshots = [snapshot_by_session[day] for day in session_dates if day in snapshot_by_session]
    feature_ready_snapshots = [snapshot for snapshot in score_snapshots if _feature_ready(snapshot)]
    trade_permitted_sessions = {
        _snapshot_session(snapshot)
        for snapshot in feature_ready_snapshots
        if _classification_eligible(snapshot)
    }
    ineligible_trade_days = sorted({trade.session_date for trade in scored_trades if trade.session_date not in trade_permitted_sessions})

    attribution_error: Optional[str] = None
    try:
        attributed = attribute_overnight_displacement_trades(scored_trades, score_ledger)
    except (KeyError, TypeError, ValueError) as error:
        attributed = ()
        attribution_error = str(error)

    daily_pnl = {day: 0.0 for day in session_dates}
    realized_r_by_session = {day: 0.0 for day in session_dates}
    trade_days: set[str] = set()
    fill_relative: list[dict[str, Any]] = []
    for item in attributed:
        trade = item.trade
        entry = item.entry
        direction = 1.0 if trade.side == "long" else -1.0
        fill_risk = (trade.entry_price - entry.stop_price) * direction
        fill_reward = (entry.target_price - trade.entry_price) * direction
        decision_risk = (entry.signal_price - entry.stop_price) * direction
        decision_reward = (entry.target_price - entry.signal_price) * direction
        risk_dollars = fill_risk * point_value * trade.quantity
        realized_r = trade.net_pnl / risk_dollars if risk_dollars > 0 else None
        row = {
            "setup_id": entry.setup_id,
            "session_date": trade.session_date,
            "entry_timestamp_utc": trade.entry_timestamp_utc,
            "signal_timestamp_utc": entry.signal_timestamp_utc,
            "symbol": trade.symbol,
            "mechanism": OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
            "side": trade.side,
            "gap_direction": entry.gap_direction,
            "gap_dtr": entry.gap_dtr,
            "displacement_breadth": entry.displacement_breadth,
            "signal_price": entry.signal_price,
            "fill_price": trade.entry_price,
            "stop_price": entry.stop_price,
            "target_price": entry.target_price,
            "prior_rth_close": entry.prior_rth_close,
            "rth_open": entry.rth_open,
            "dtr20": entry.dtr20,
            "adverse_entry_gap_points": (trade.entry_price - entry.signal_price) * direction,
            "decision_risk_points": decision_risk,
            "decision_risk_dtr": decision_risk / entry.dtr20 if entry.dtr20 > 0 else None,
            "decision_target_reward_points": decision_reward,
            "decision_target_reward_r": decision_reward / decision_risk if decision_risk > 0 else None,
            "fill_risk_points": fill_risk,
            "fill_target_reward_points": fill_reward,
            "fill_target_reward_r": fill_reward / fill_risk if fill_risk > 0 else None,
            "target_behind_or_equal_fill": fill_reward <= 0,
            "stop_invalid_or_equal_fill": fill_risk <= 0,
            "realized_net_r": realized_r,
        }
        fill_relative.append(row)
        daily_pnl[trade.session_date] += trade.net_pnl
        if realized_r is not None:
            realized_r_by_session[trade.session_date] += realized_r
        trade_days.add(trade.session_date)

    # Even a corrupt/unattributed trade remains in dollar score series; the
    # integrity gate fails separately instead of improving results by omission.
    attributed_trade_ids = {id(item.trade) for item in attributed}
    for trade in scored_trades:
        if id(trade) not in attributed_trade_ids:
            daily_pnl.setdefault(trade.session_date, 0.0)
            daily_pnl[trade.session_date] += trade.net_pnl
            trade_days.add(trade.session_date)

    eligible_events = [event for event in diagnostic_events if _field(event, "session_date") in score_session_set]
    active_rth_continuity = _audit_active_rth_continuity(
        ledger=score_ledger,
        snapshots=score_snapshots,
        diagnostic_events=eligible_events,
    )
    active_gap_sessions = sorted(
        set(active_rth_continuity["reconstructed_gap_sessions"])
        | set(active_rth_continuity["diagnostic_gap_sessions"])
    )
    state_completeness = _audit_state_completeness(
        snapshots=score_snapshots,
        diagnostic_events=eligible_events,
    )
    snapshot_integrity = _audit_snapshot_calendar_and_roll(score_snapshots)
    dtr_bar_ledger_audit = _audit_dtr_against_bar_ledger(
        ledger=ledger,
        snapshots=score_snapshots,
    )
    overnight_bar_ledger_audit = _audit_overnight_against_bar_ledger(
        ledger=ledger,
        snapshots=score_snapshots,
    )
    reconciliation = audit_overnight_displacement_reconciliation(
        ledger=score_ledger,
        trades=scored_trades,
        diagnostic_events=eligible_events,
        point_value=point_value,
        expected_entry_delay_bars=expected_entry_delay_bars,
        commission_per_contract_round_trip=commission_per_contract_round_trip,
        entry_slippage_points=entry_slippage_points,
        exit_slippage_points=exit_slippage_points,
        tick_size=tick_size,
        attribution_error=attribution_error,
    )
    violations = list(reconciliation["violations"])
    if pre_warmup_or_out_of_score_trades:
        violations.append(
            "trades_before_effective_warmup_or_outside_score_window:"
            + ",".join(sorted({trade.session_date for trade in pre_warmup_or_out_of_score_trades}))
        )
    if ineligible_trade_days:
        violations.append(f"trades_outside_eligible_sessions:{','.join(ineligible_trade_days)}")
    if active_gap_sessions:
        violations.append("active_rth_minute_gap")
    if active_rth_continuity["diagnostic_missing_for_reconstructed_gap"]:
        violations.append("active_rth_gap_missing_strategy_diagnostic")
    if active_rth_continuity["diagnostic_without_reconstructed_gap"]:
        violations.append("active_rth_gap_diagnostic_disagrees_with_bar_ledger")
    violations.extend(state_completeness["violations"])
    violations.extend(snapshot_integrity["violations"])
    violations.extend(dtr_bar_ledger_audit["violations"])
    violations.extend(overnight_bar_ledger_audit["violations"])
    reconciliation["violations"] = list(dict.fromkeys(violations))
    reconciliation["violation_count"] = len(reconciliation["violations"])

    daily_series = [daily_pnl[day] for day in session_dates]
    daily = build_daily_metrics({day: daily_pnl[day] for day in trade_days}, list(session_dates)).to_dict()
    weekly = _weekly_report(
        session_dates=session_dates,
        daily_pnl=daily_pnl,
        realized_r_by_session=realized_r_by_session,
    )
    bootstrap = build_block_bootstrap_report(
        daily_series,
        block_length_sessions=10,
        draws=20_000,
        seed=20260712,
    ).to_dict()
    confidence = build_sharpe_confidence(
        daily_series,
        candidate_family_trial_budget=candidate_family_trial_budget,
    ).to_dict()
    half_years = _complete_half_years(
        score_start_session=score_start_session,
        score_end_session_exclusive=score_end_session_exclusive,
        session_dates=session_dates,
        daily_pnl=daily_pnl,
        trades=scored_trades,
        point_value=point_value,
    )

    entry_by_trade = {id(item.trade): item.entry for item in attributed}
    snapshot_for_trade = {trade.session_date: snapshot_by_session.get(trade.session_date) for trade in scored_trades}

    def feature_for_trade(trade: Trade, name: str) -> Any:
        snapshot = snapshot_for_trade.get(trade.session_date)
        return _field(_field(snapshot, "features"), name) if snapshot is not None else None

    def signal_time(trade: Trade) -> str:
        entry = entry_by_trade.get(id(trade))
        timestamp = entry.signal_timestamp_utc if entry is not None else trade.entry_timestamp_utc
        info = classify_timestamp(timestamp)
        return f"{info.minutes_from_midnight_et // 60:02d}:{info.minutes_from_midnight_et % 60:02d}"

    by_side = _grouped_trades(scored_trades, key=lambda trade: trade.side, point_value=point_value)
    slices = {
        "year": _grouped_trades(scored_trades, key=lambda trade: trade.session_date[:4], point_value=point_value),
        "half_year": _grouped_trades(scored_trades, key=lambda trade: f"{trade.session_date[:4]}-H{1 if int(trade.session_date[5:7]) <= 6 else 2}", point_value=point_value),
        "month": _grouped_trades(scored_trades, key=lambda trade: trade.session_date[:7], point_value=point_value),
        "weekday": _grouped_trades(scored_trades, key=lambda trade: date.fromisoformat(trade.session_date).strftime("%A"), point_value=point_value),
        "signal_time": _grouped_trades(scored_trades, key=signal_time, point_value=point_value),
        "gap_size_dtr": _grouped_trades(scored_trades, key=lambda trade: _gap_bucket(float(feature_for_trade(trade, "gap_dtr")) if _finite(feature_for_trade(trade, "gap_dtr")) else None), point_value=point_value),
        "displacement_breadth": _grouped_trades(scored_trades, key=lambda trade: _breadth_bucket(float(feature_for_trade(trade, "displacement_breadth")) if _finite(feature_for_trade(trade, "displacement_breadth")) else None), point_value=point_value),
        "roll": _grouped_trades(scored_trades, key=lambda trade: "roll" if bool(feature_for_trade(trade, "roll_transition")) else "non_roll", point_value=point_value),
        "rth_schedule": _grouped_trades(scored_trades, key=lambda trade: "regular_16:00" if rth_close_minutes_et(date.fromisoformat(trade.session_date)) == 960 else f"abbreviated_{rth_close_minutes_et(date.fromisoformat(trade.session_date))}", point_value=point_value),
        "exit_reason": _grouped_trades(scored_trades, key=lambda trade: trade.exit_reason, point_value=point_value),
    }

    classifier_counts = Counter(
        f"{_enum_value(_field(_field(snapshot, 'classification'), 'regime', 'unknown'))}:{_enum_value(_field(_field(snapshot, 'classification'), 'side', 'none'))}"
        for snapshot in score_snapshots
    )
    classifier_reasons = Counter(
        str(_field(_field(snapshot, "classification"), "reason", "unknown"))
        for snapshot in score_snapshots
    )
    diagnostic_funnel = Counter(str(_field(event, "event")) for event in eligible_events)
    gap_eligible_count = sum(
        _finite(_field(_field(snapshot, "features"), "gap_dtr"))
        and 0.05 <= float(_field(_field(snapshot, "features"), "gap_dtr")) <= 0.75
        for snapshot in score_snapshots
    )
    breadth_aligned_count = sum(
        _finite(_field(_field(snapshot, "features"), "displacement_breadth"))
        and float(_field(_field(snapshot, "features"), "displacement_breadth")) >= 0.50
        for snapshot in score_snapshots
    )
    funnel = {
        "expected_score_sessions": len(session_dates),
        "snapshot_sessions": len(score_snapshots),
        "prior_rth_complete": sum(bool(_field(_field(snapshot, "features"), "prior_rth_complete", False)) for snapshot in score_snapshots),
        "overnight_complete": sum(bool(_field(_field(snapshot, "features"), "complete_overnight", False)) for snapshot in score_snapshots),
        "non_roll": sum(not bool(_field(_field(snapshot, "features"), "roll_transition", False)) for snapshot in score_snapshots),
        "gap_eligible": gap_eligible_count,
        "displacement_aligned": breadth_aligned_count,
        **dict(sorted(diagnostic_funnel.items())),
        "intent": reconciliation["order_intent_count"],
        "fill": reconciliation["entry_fill_count"],
        "trade_close": reconciliation["trade_count"],
    }
    terminal_untraded = Counter(
        str(_field(_field(snapshot, "classification"), "reason", "unknown"))
        for snapshot in score_snapshots
        if not _classification_eligible(snapshot)
    )
    for event in eligible_events:
        if _field(event, "event") in ("entry_cancelled", "entry_rejected", "entry_pending_expired"):
            terminal_untraded[str(_metadata(event).get("reason", _field(event, "event")))] += 1

    feature_ready_features = [_field(snapshot, "features") for snapshot in feature_ready_snapshots]
    signal_intents = [
        record.payload
        for record in score_ledger.records
        if record.event_type == EventType.ORDER_INTENT and record.payload.get("reason") in ENTRY_REASONS
    ]
    feature_distributions = {
        "dtr20_points": _distribution(_field(features, "dtr20") for features in feature_ready_features),
        "gap_signed_points": _distribution(_field(features, "gap_signed_points") for features in feature_ready_features),
        "gap_dtr": _distribution(_field(features, "gap_dtr") for features in feature_ready_features),
        "displacement_breadth": _distribution(_field(features, "displacement_breadth") for features in feature_ready_features),
        "breadth_above_prior_close": _distribution(_field(features, "breadth_above_prior_close") for features in feature_ready_features),
        "breadth_below_prior_close": _distribution(_field(features, "breadth_below_prior_close") for features in feature_ready_features),
        "overnight_total_volume": _distribution(_field(features, "overnight_total_volume") for features in feature_ready_features),
        "overnight_range_points": _distribution(_field(features, "overnight_range") for features in feature_ready_features),
        "overnight_close_relative_to_prior": _distribution(float(_field(features, "overnight_close")) - float(_field(features, "prior_rth_close")) for features in feature_ready_features if _finite(_field(features, "overnight_close")) and _finite(_field(features, "prior_rth_close"))),
        "overnight_vwap_relative_to_prior": _distribution(float(_field(features, "overnight_vwap")) - float(_field(features, "prior_rth_close")) for features in feature_ready_features if _finite(_field(features, "overnight_vwap")) and _finite(_field(features, "prior_rth_close"))),
        "overnight_high_relative_to_prior": _distribution(float(_field(features, "overnight_high")) - float(_field(features, "prior_rth_close")) for features in feature_ready_features if _finite(_field(features, "overnight_high")) and _finite(_field(features, "prior_rth_close"))),
        "overnight_low_relative_to_prior": _distribution(float(_field(features, "overnight_low")) - float(_field(features, "prior_rth_close")) for features in feature_ready_features if _finite(_field(features, "overnight_low")) and _finite(_field(features, "prior_rth_close"))),
        "extension_magnitude_dtr": _distribution(payload.get("extension_magnitude_dtr") for payload in signal_intents),
        "decisive_cross_distance_dtr": _distribution(payload.get("decisive_cross_distance_dtr") for payload in signal_intents),
        "close_location": _distribution(payload.get("close_location") for payload in signal_intents),
        "structural_extreme": _distribution(payload.get("structural_extreme") for payload in signal_intents),
        "decision_risk_dtr": _distribution(payload.get("decision_risk_dtr") for payload in signal_intents),
        "target_distance_r": _distribution(payload.get("target_distance_r") for payload in signal_intents),
    }

    commission_drag = sum(trade.commission for trade in scored_trades)
    slippage_drag = sum(
        float(record.payload.get("slippage_points", 0.0))
        * point_value
        * int(record.payload.get("quantity", 0))
        for record in score_ledger.records
        if record.event_type == EventType.FILL
    )
    score_rth_minutes = sum(
        max(0, int(rth_close_minutes_et(date.fromisoformat(day)) or 570) - 570)
        for day in session_dates
    )
    overall = _summary(
        scored_trades,
        point_value=point_value,
        score_rth_minutes=score_rth_minutes,
    )
    observed_net = float(overall["survivability"]["net_pnl"])
    observed_annualized_net = observed_net * 252.0 / len(session_dates) if session_dates else 0.0
    p99_abs = abs(float(bootstrap["max_drawdown_p99_adverse"]))
    capital_evaluated = allocated_capital is not None and hard_loss_limit is not None
    capital_limit = min(0.25 * allocated_capital, 0.50 * hard_loss_limit) if capital_evaluated else None
    capital_passed = p99_abs <= capital_limit if capital_limit is not None else None
    portfolio_overlap = _portfolio_overlap_report(
        session_dates=session_dates,
        candidate_daily_pnl=daily_pnl,
        comparison_daily_pnl=comparison_daily_pnl,
        comparison_name=comparison_name,
        comparison_provenance=comparison_provenance,
    )

    report: dict[str, Any] = {
        "score_window": {
            "requested_start_session": score_start_session,
            "effective_start_session_after_causal_warmup": effective_start,
            "causal_warmup_ready_ordinal": warmup_ordinal,
            "end_session_exclusive": score_end_session_exclusive,
            "expected_sessions_from_requested_start": len(expected),
            "expected_score_sessions_after_warmup": len(session_dates),
            "snapshot_sessions_total_audit": len(scored_snapshots),
            "snapshot_score_sessions": len(score_snapshots),
            "feature_ready_setup_sessions": len(feature_ready_snapshots),
            "fail_closed_or_missing_score_sessions": len(session_dates) - len(feature_ready_snapshots),
            "warmup_expected_sessions": (warmup_ordinal - 1) if warmup_ordinal is not None else len(expected),
            "missing_expected_sessions": missing,
            "missing_expected_session_count": len(missing),
            "unexpected_snapshot_sessions": unexpected,
            "unexpected_snapshot_session_count": len(unexpected),
            "active_rth_minute_gap_sessions": active_gap_sessions,
            "active_rth_minute_gap_session_count": len(active_gap_sessions),
            "active_rth_continuity_audit": active_rth_continuity,
        },
        "overall": overall,
        "mechanism_counts": {OVERNIGHT_DISPLACEMENT_REVERSAL_REASON: len(attributed)},
        "by_side": by_side,
        "slices": slices,
        "complete_half_years": half_years,
        "daily": daily,
        "weekly": weekly,
        "monthly": {
            month: {
                "net_pnl": sum(daily_pnl[day] for day in session_dates if day[:7] == month),
                "score_sessions": sum(day[:7] == month for day in session_dates),
                "trade_days": sum(day[:7] == month and day in trade_days for day in session_dates),
            }
            for month in sorted({day[:7] for day in session_dates})
        },
        "bootstrap": bootstrap,
        "statistical_confidence": confidence,
        "portfolio_overlap": portfolio_overlap,
        "allocated_capital_returns": (
            {
                "status": "available",
                "daily_return_fraction": _distribution(daily_pnl[day] / allocated_capital for day in session_dates),
                "weekly_return_fraction": _distribution(row["net_pnl"] / allocated_capital for row in weekly["weeks"]),
            }
            if allocated_capital is not None
            else {"status": "unavailable_no_allocated_capital"}
        ),
        "session_t_stat": _t_stat(daily_series),
        "risk_efficiency": {
            "observed_annualized_net_pnl": observed_annualized_net,
            "bootstrap_p95_drawdown": bootstrap["max_drawdown_p95_adverse"],
            "bootstrap_p99_drawdown": bootstrap["max_drawdown_p99_adverse"],
            "capital_policy": {
                "allocated_capital": allocated_capital,
                "hard_loss_limit": hard_loss_limit,
                "maximum_permitted_p99_drawdown": capital_limit,
                "evaluated": capital_evaluated,
                "passed": capital_passed,
            },
        },
        "execution_model": {
            "point_value": point_value,
            "commission_per_contract_round_trip": commission_per_contract_round_trip,
            "entry_slippage_points": entry_slippage_points,
            "exit_slippage_points": exit_slippage_points,
            "rth_open_extra_entry_slippage_points": 0.0,
            "tick_size": tick_size,
            "fill_timing": "next_bar_open",
            "expected_entry_delay_bars": expected_entry_delay_bars,
        },
        "classifier_counts": dict(sorted(classifier_counts.items())),
        "classifier_reasons": dict(sorted(classifier_reasons.items())),
        "funnel": funnel,
        "terminal_untraded_reasons": dict(sorted(terminal_untraded.items())),
        "feature_distributions": feature_distributions,
        "fill_relative_trades": fill_relative,
        "execution_diagnostics": {
            "target_behind_or_equal_fill_count": sum(row["target_behind_or_equal_fill"] for row in fill_relative),
            "stop_invalid_or_equal_fill_count": sum(row["stop_invalid_or_equal_fill"] for row in fill_relative),
            "entry_invalidated_at_fill_count": reconciliation["entry_invalidated_at_fill_count"],
            "risk_veto_count": reconciliation["entry_risk_veto_count"],
            "ambiguous_exit_count": sum(trade.ambiguous_exit for trade in scored_trades),
            "modeled_commission_drag_dollars": commission_drag,
            "modeled_slippage_drag_dollars": slippage_drag,
            "modeled_total_cost_drag_dollars": commission_drag + slippage_drag,
            "adverse_entry_gap_points": _distribution(row["adverse_entry_gap_points"] for row in fill_relative),
            "fill_risk_points": _distribution(row["fill_risk_points"] for row in fill_relative),
            "fill_target_reward_r": _distribution(row["fill_target_reward_r"] for row in fill_relative),
            "realized_net_r": _distribution(row["realized_net_r"] for row in fill_relative),
            "reconciliation_violations": reconciliation["violations"],
            "reconciliation_violation_count": reconciliation["violation_count"],
            "reconciliation": reconciliation,
            "state_completeness": state_completeness,
            "snapshot_calendar_and_roll_audit": snapshot_integrity,
            "dtr_bar_ledger_audit": dtr_bar_ledger_audit,
            "overnight_bar_ledger_audit": overnight_bar_ledger_audit,
        },
        "deterministic_replay": {
            "verified": False,
            "core_hashes": {},
            "mismatches": ["second_replay_not_supplied"],
        },
    }
    report["t1_primary_gates"] = evaluate_odr_t1_primary_gates(report)
    report["promotion_status"] = (
        "primary_qualified_robustness_capital_and_prospective_pending"
        if report["t1_primary_gates"]["passed"]
        else "rejected_primary_no_threshold_rescue"
    )
    return report
