"""Attribution, diagnostics, and promotion gates for Opening Auction Retest v2."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
import statistics
from typing import Any, Iterable, Optional, Sequence

from full_python.events import EventLedger, EventType
from full_python.data.exchange_calendar import rth_close_minutes_et
from full_python.models import Trade
from full_python.reporting.bootstrap import build_block_bootstrap_report
from full_python.reporting.metrics import build_metrics_report
from full_python.reporting.survivability import (
    TradeResult,
    build_daily_metrics,
    build_survivability_report,
)
from full_python.research.statistical_confidence import build_sharpe_confidence
from full_python.strategy.opening_auction_retest import (
    ACCEPTED_BREAK_REASON,
    REJECTED_BREAK_REASON,
    RetestDiagnosticEvent,
    RetestSessionSnapshot,
)


ENTRY_REASONS = (ACCEPTED_BREAK_REASON, REJECTED_BREAK_REASON)


@dataclass(frozen=True)
class RetestEntryAttribution:
    entry_timestamp_utc: str
    symbol: str
    side: str
    quantity: int
    fill_price: float
    entry_reason: str
    branch: str
    signal_timestamp_utc: str
    signal_price: float
    stop_price: float
    target_price: float
    reference_side: Optional[str]
    reference_type: Optional[str]
    reference_price: float


@dataclass(frozen=True)
class AttributedRetestTrade:
    trade: Trade
    entry: RetestEntryAttribution


def _entry_attributions(ledger: EventLedger) -> list[RetestEntryAttribution]:
    pending: Optional[dict[str, Any]] = None
    result: list[RetestEntryAttribution] = []
    for record in ledger.records:
        payload = record.payload
        if (
            record.event_type == EventType.ORDER_INTENT
            and payload.get("reason") in ENTRY_REASONS
        ):
            if pending is not None:
                raise ValueError("overlapping retest order intents")
            pending = {
                "timestamp": record.timestamp_utc,
                "symbol": str(payload["symbol"]),
                "side": str(payload["side"]),
                "reason": str(payload["reason"]),
                "quantity": int(payload["quantity"]),
                "signal_price": float(payload["signal_price"]),
                "stop_price": float(payload["stop_price"]),
                "target_price": float(payload["target_price"]),
                "reference_side": payload.get("reference_side"),
                "reference_type": payload.get("reference_type"),
                "reference_price": float(payload["reference_price"]),
            }
            continue
        if (
            record.event_type == EventType.STATE_TRANSITION
            and pending is not None
            and payload.get("transition")
            in ("entry_invalidated_at_fill", "entry_missed", "pending_orders_cancelled")
        ):
            pending = None
            continue
        if record.event_type == EventType.FILL and payload.get("reason") in ENTRY_REASONS:
            if pending is None:
                raise ValueError("entry fill has no attributable retest order intent")
            fill_side = "long" if payload["side"] == "buy" else "short"
            intended_side = "long" if pending["side"] == "buy" else "short"
            if fill_side != intended_side or payload["symbol"] != pending["symbol"]:
                raise ValueError("entry fill does not match retest order intent")
            if int(payload["quantity"]) != int(pending["quantity"]):
                raise ValueError("entry fill quantity does not match retest order intent")
            reason = str(payload["reason"])
            result.append(
                RetestEntryAttribution(
                    entry_timestamp_utc=record.timestamp_utc,
                    symbol=str(payload["symbol"]),
                    side=fill_side,
                    quantity=int(payload["quantity"]),
                    fill_price=float(payload["price"]),
                    entry_reason=reason,
                    branch=(
                        "accepted_break"
                        if reason == ACCEPTED_BREAK_REASON
                        else "rejected_break"
                    ),
                    signal_timestamp_utc=str(pending["timestamp"]),
                    signal_price=float(pending["signal_price"]),
                    stop_price=float(pending["stop_price"]),
                    target_price=float(pending["target_price"]),
                    reference_side=(
                        None
                        if pending["reference_side"] is None
                        else str(pending["reference_side"])
                    ),
                    reference_type=(
                        None
                        if pending["reference_type"] is None
                        else str(pending["reference_type"])
                    ),
                    reference_price=float(pending["reference_price"]),
                )
            )
            pending = None
    return result


def attribute_retest_trades(
    trades: Sequence[Trade], ledger: EventLedger
) -> tuple[AttributedRetestTrade, ...]:
    entries: dict[tuple[str, str, str], RetestEntryAttribution] = {}
    for entry in _entry_attributions(ledger):
        key = (entry.entry_timestamp_utc, entry.symbol, entry.side)
        if key in entries:
            raise ValueError(f"duplicate retest entry attribution: {key}")
        entries[key] = entry
    attributed: list[AttributedRetestTrade] = []
    for trade in trades:
        key = (trade.entry_timestamp_utc, trade.symbol, trade.side)
        entry = entries.pop(key, None)
        if entry is None:
            raise ValueError(f"trade has no retest entry attribution: {key}")
        if trade.quantity != entry.quantity:
            raise ValueError(f"trade quantity does not match entry attribution: {key}")
        if trade.entry_price != entry.fill_price:
            raise ValueError(f"trade entry price does not match entry fill: {key}")
        if trade.stop_price != entry.stop_price:
            raise ValueError(f"trade stop does not match frozen entry intent: {key}")
        attributed.append(AttributedRetestTrade(trade, entry))
    if entries:
        raise ValueError("filled retest entries did not produce closed trades")
    return tuple(attributed)


def _t_stat(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    deviation = statistics.stdev(values)
    if deviation == 0:
        return None
    return statistics.mean(values) / (deviation / math.sqrt(len(values)))


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _summary(
    trades: Sequence[Trade], *, point_value: float, eligible_session_count: int = 0
) -> dict[str, Any]:
    survivability = build_survivability_report(
        [TradeResult(t.exit_timestamp_utc, t.side, t.net_pnl) for t in trades]
    )
    risks = [abs(trade.entry_price - trade.stop_price) for trade in trades]
    mfe_r = [
        trade.mfe_points / risk for trade, risk in zip(trades, risks) if risk > 0
    ]
    mae_r = [
        trade.mae_points / risk for trade, risk in zip(trades, risks) if risk > 0
    ]
    holding_minutes = [
        (_parse_utc(trade.exit_timestamp_utc) - _parse_utc(trade.entry_timestamp_utc)).total_seconds()
        / 60.0
        for trade in trades
    ]
    total_holding = sum(holding_minutes)
    return {
        "survivability": survivability.to_dict(),
        "metrics": build_metrics_report(trades, point_value=point_value).to_dict(),
        "trade_pnl_dollars": _distribution(trade.net_pnl for trade in trades),
        "trade_t_stat": _t_stat([trade.net_pnl for trade in trades]),
        "holding_time_minutes": {
            "mean": statistics.mean(holding_minutes) if holding_minutes else None,
            "median": statistics.median(holding_minutes) if holding_minutes else None,
            "max": max(holding_minutes, default=None),
            "total": total_holding,
        },
        "exposure_fraction_of_rth_minutes": (
            total_holding / (eligible_session_count * 390.0)
            if eligible_session_count > 0
            else None
        ),
        "excursions": {
            "mean_mfe_points": (
                statistics.mean(t.mfe_points for t in trades) if trades else None
            ),
            "median_mfe_points": (
                statistics.median(t.mfe_points for t in trades) if trades else None
            ),
            "mean_mae_points": (
                statistics.mean(t.mae_points for t in trades) if trades else None
            ),
            "median_mae_points": (
                statistics.median(t.mae_points for t in trades) if trades else None
            ),
            "mean_mfe_r": statistics.mean(mfe_r) if mfe_r else None,
            "median_mfe_r": statistics.median(mfe_r) if mfe_r else None,
            "mean_mae_r": statistics.mean(mae_r) if mae_r else None,
            "median_mae_r": statistics.median(mae_r) if mae_r else None,
        },
    }


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
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(finite),
        "min": min(finite, default=None),
        "p05": _quantile(finite, 0.05),
        "p25": _quantile(finite, 0.25),
        "median": _quantile(finite, 0.50),
        "mean": statistics.mean(finite) if finite else None,
        "p75": _quantile(finite, 0.75),
        "p95": _quantile(finite, 0.95),
        "max": max(finite, default=None),
    }


def _feature_ready(snapshot: RetestSessionSnapshot) -> bool:
    features = snapshot.features
    required = (
        features.dtr20,
        features.rth_open,
        features.opening_high,
        features.opening_low,
        features.opening_close,
        features.opening_vwap,
        features.overnight_high,
        features.overnight_low,
        features.prior_rth_high,
        features.prior_rth_low,
        features.prior_rth_close,
    )
    return bool(
        features.complete_observation
        and features.complete_overnight
        and not features.roll_transition
        and len(features.opening_closes) == 15
        and all(value is not None and math.isfinite(float(value)) for value in required)
        and float(features.dtr20) > 0
    )


def _warmup_ready(snapshot: RetestSessionSnapshot) -> bool:
    features = snapshot.features
    required = (
        features.dtr20,
        features.prior_rth_high,
        features.prior_rth_low,
        features.prior_rth_close,
    )
    return bool(
        all(value is not None and math.isfinite(float(value)) for value in required)
        and float(features.dtr20) > 0
    )


def _expected_cme_sessions(start_session: str, end_session_exclusive: str) -> list[str]:
    cursor = date.fromisoformat(start_session)
    end = date.fromisoformat(end_session_exclusive)
    result: list[str] = []
    while cursor < end:
        if rth_close_minutes_et(cursor) is not None:
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def _grouped(
    attributed: Sequence[AttributedRetestTrade], *, key, point_value: float
) -> dict[str, Any]:
    buckets: dict[str, list[Trade]] = {}
    for item in attributed:
        buckets.setdefault(str(key(item)), []).append(item.trade)
    return {
        name: _summary(group, point_value=point_value)
        for name, group in sorted(buckets.items())
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
    result: dict[str, Any] = {}
    for year in range(start.year, end.year + 1):
        for half, fold_start, fold_end in (
            (1, date(year, 1, 1), date(year, 7, 1)),
            (2, date(year, 7, 1), date(year + 1, 1, 1)),
        ):
            if fold_start < start or fold_end > end:
                continue
            key = f"{year}-H{half}"
            days = [day for day in session_dates if fold_start <= date.fromisoformat(day) < fold_end]
            fold_trades = [
                trade
                for trade in trades
                if fold_start <= date.fromisoformat(trade.session_date) < fold_end
            ]
            summary = _summary(
                fold_trades,
                point_value=point_value,
                eligible_session_count=len(days),
            )
            result[key] = {
                "start_session": fold_start.isoformat(),
                "end_session_exclusive": fold_end.isoformat(),
                "eligible_sessions": len(days),
                "net_pnl_including_zero_days": sum(daily_pnl[day] for day in days),
                **summary,
            }
    return result


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
    ordered = sorted(pnl)
    pnl_series = [pnl[key] for key in ordered]
    r_series = [r_values[key] for key in ordered]
    rolling_13_pnl = [sum(pnl_series[index - 12 : index + 1]) for index in range(12, len(ordered))]
    rolling_13_r = [sum(r_series[index - 12 : index + 1]) for index in range(12, len(ordered))]
    return {
        "week_count": len(ordered),
        "weeks": [
            {"week": key, "net_pnl": pnl[key], "realized_net_r": r_values[key]}
            for key in ordered
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
            "net_pnl": _distribution(rolling_13_pnl),
            "realized_net_r": _distribution(rolling_13_r),
        },
    }


def _pf_pass(summary: dict[str, Any], threshold: float) -> bool:
    survivability = summary.get("survivability", {})
    if survivability.get("trade_count", 0) <= 0:
        return False
    value = survivability.get("profit_factor")
    return bool(
        (value is None and survivability.get("net_pnl", 0.0) > 0)
        or (value is not None and float(value) >= threshold)
    )


def _audit_execution_reconciliation(
    *,
    ledger: EventLedger,
    trades: Sequence[Trade],
    diagnostic_events: Sequence[RetestDiagnosticEvent],
    point_value: float,
    expected_entry_delay_bars: int,
    target_behind_fill_count: int,
) -> dict[str, Any]:
    records = ledger.records
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
    intents = [
        (index, record)
        for index, record in enumerate(records)
        if record.event_type == EventType.ORDER_INTENT
        and record.payload.get("reason") in ENTRY_REASONS
    ]
    entry_fills = [
        (index, record)
        for index, record in enumerate(records)
        if record.event_type == EventType.FILL
        and record.payload.get("reason") in ENTRY_REASONS
    ]
    exit_fills = [
        record
        for record in records
        if record.event_type == EventType.FILL
        and record.payload.get("reason") not in ENTRY_REASONS
    ]
    trade_records = [
        record for record in records if record.event_type == EventType.TRADE_CLOSED
    ]
    risk_vetoes = [
        record
        for record in records
        if record.event_type == EventType.RISK_VETO
        and record.payload.get("reason") in ENTRY_REASONS
    ]
    outcome_transitions = [
        record
        for record in records
        if record.event_type == EventType.STATE_TRANSITION
        and record.payload.get("transition")
        in ("entry_invalidated_at_fill", "entry_missed", "pending_orders_cancelled")
    ]
    invalidated_at_fill = sum(
        record.payload.get("transition") == "entry_invalidated_at_fill"
        for record in outcome_transitions
    )
    confirmations = [
        event for event in diagnostic_events if event.event == "entry_confirmed"
    ]
    diagnostic_fills = [event for event in diagnostic_events if event.event == "filled"]
    violations: list[str] = []

    def add_if(condition: bool, name: str) -> None:
        if condition:
            violations.append(name)

    add_if(len(confirmations) != len(signals), "confirmation_signal_count_mismatch")
    add_if(len(signals) != len(intents) + len(risk_vetoes), "signal_intent_outcome_count_mismatch")
    add_if(bool(risk_vetoes), "accepted_entry_risk_veto")
    add_if(
        len(intents) != len(entry_fills) + len(outcome_transitions),
        "intent_terminal_outcome_count_mismatch",
    )
    add_if(len(entry_fills) != len(trades), "entry_fill_trade_count_mismatch")
    add_if(len(diagnostic_fills) != len(entry_fills), "diagnostic_fill_count_mismatch")
    add_if(len(exit_fills) != len(trades), "exit_fill_trade_count_mismatch")
    add_if(len(trade_records) != len(trades), "trade_ledger_count_mismatch")
    add_if(invalidated_at_fill > 0, "entry_invalidated_at_fill")
    add_if(target_behind_fill_count > 0, "target_not_protective_relative_to_fill")

    for pair_index, ((_, signal), (intent_index, intent)) in enumerate(
        zip(signals, intents), start=1
    ):
        expected_side = "buy" if signal.payload.get("side") == "long" else "sell"
        add_if(
            signal.timestamp_utc != intent.timestamp_utc,
            f"signal_intent_timestamp_mismatch_{pair_index}",
        )
        add_if(
            signal.payload.get("symbol") != intent.payload.get("symbol"),
            f"signal_intent_symbol_mismatch_{pair_index}",
        )
        add_if(
            signal.payload.get("reason") != intent.payload.get("reason"),
            f"signal_intent_reason_mismatch_{pair_index}",
        )
        add_if(
            expected_side != intent.payload.get("side"),
            f"signal_intent_side_mismatch_{pair_index}",
        )
        for field_name in ("signal_price", "stop_price", "target_price"):
            add_if(
                signal.payload.get(field_name) != intent.payload.get(field_name),
                f"signal_intent_{field_name}_mismatch_{pair_index}",
            )

        if pair_index <= len(entry_fills):
            fill_index, fill = entry_fills[pair_index - 1]
            expected_bar_ordinal = expected_entry_delay_bars + 1
            first_later_bar = bisect_right(bar_positions, intent_index)
            expected_position = first_later_bar + expected_bar_ordinal - 1
            expected_timestamp = (
                records[bar_positions[expected_position]].timestamp_utc
                if expected_position < len(bar_positions)
                else None
            )
            add_if(
                fill.timestamp_utc != expected_timestamp,
                f"entry_fill_timing_mismatch_{pair_index}",
            )
            add_if(
                (
                    bisect_left(bar_positions, fill_index) - first_later_bar
                    != expected_entry_delay_bars + 1
                ),
                f"entry_fill_intervening_bar_count_mismatch_{pair_index}",
            )
            add_if(
                fill.payload.get("symbol") != intent.payload.get("symbol"),
                f"intent_fill_symbol_mismatch_{pair_index}",
            )
            add_if(
                fill.payload.get("side") != intent.payload.get("side"),
                f"intent_fill_side_mismatch_{pair_index}",
            )
            add_if(
                fill.payload.get("quantity") != intent.payload.get("quantity"),
                f"intent_fill_quantity_mismatch_{pair_index}",
            )
            add_if(
                fill.payload.get("reason") != intent.payload.get("reason"),
                f"intent_fill_reason_mismatch_{pair_index}",
            )

    for index, trade in enumerate(trades, start=1):
        matching_exit_fills = [
            fill
            for fill in exit_fills
            if fill.timestamp_utc == trade.exit_timestamp_utc
            and fill.payload.get("symbol") == trade.symbol
            and fill.payload.get("quantity") == trade.quantity
            and fill.payload.get("price") == trade.exit_price
            and fill.payload.get("reason") == trade.exit_reason
        ]
        add_if(len(matching_exit_fills) != 1, f"trade_exit_fill_mismatch_{index}")
        expected_exit_side = "sell" if trade.side == "long" else "buy"
        if matching_exit_fills:
            add_if(
                matching_exit_fills[0].payload.get("side") != expected_exit_side,
                f"trade_exit_side_mismatch_{index}",
            )
        expected_gross_points = (
            trade.exit_price - trade.entry_price
            if trade.side == "long"
            else trade.entry_price - trade.exit_price
        )
        expected_gross_pnl = expected_gross_points * point_value * trade.quantity
        add_if(
            not math.isclose(trade.gross_points, expected_gross_points, abs_tol=1e-9),
            f"trade_gross_points_mismatch_{index}",
        )
        add_if(
            not math.isclose(trade.gross_pnl, expected_gross_pnl, abs_tol=1e-9),
            f"trade_gross_pnl_mismatch_{index}",
        )
        add_if(
            not math.isclose(trade.net_pnl, trade.gross_pnl - trade.commission, abs_tol=1e-9),
            f"trade_net_pnl_mismatch_{index}",
        )
        matching_trade_records = [
            record for record in trade_records if record.payload == trade.to_payload()
        ]
        add_if(len(matching_trade_records) != 1, f"trade_ledger_payload_mismatch_{index}")

    return {
        "accepted_signal_count": len(signals),
        "order_intent_count": len(intents),
        "entry_fill_count": len(entry_fills),
        "exit_fill_count": len(exit_fills),
        "trade_count": len(trades),
        "trade_ledger_count": len(trade_records),
        "entry_risk_veto_count": len(risk_vetoes),
        "entry_terminal_transition_count": len(outcome_transitions),
        "entry_invalidated_at_fill_count": invalidated_at_fill,
        "violations": violations,
        "violation_count": len(violations),
    }


def evaluate_retest_t1_primary_gates(report: dict[str, Any]) -> dict[str, Any]:
    overall = report["overall"]["survivability"]
    branches = report["by_branch"]
    sides = report["by_side"]
    daily = report["daily"]
    weekly = report["weekly"]
    bootstrap = report["bootstrap"]
    confidence = report["statistical_confidence"]
    half_years = report["complete_half_years"]
    positive_folds = sum(
        fold["net_pnl_including_zero_days"] > 0 for fold in half_years.values()
    )
    required_positive_folds = math.ceil(0.70 * len(half_years)) if half_years else 1
    final_fold = half_years[sorted(half_years)[-1]] if half_years else None
    p95_drawdown = float(bootstrap["max_drawdown_p95_adverse"])
    annualized_net = float(report["risk_efficiency"]["observed_annualized_net_pnl"])
    annualized_net_to_p95 = (
        annualized_net / abs(p95_drawdown) if p95_drawdown < 0 else None
    )
    annualized_net_to_p95_pass = bool(
        (annualized_net_to_p95 is not None and annualized_net_to_p95 >= 1.0)
        or (p95_drawdown == 0 and annualized_net > 0)
    )
    traded_years = {
        trade["session_date"][:4] for trade in report["fill_relative_trades"]
    }

    branch_names = ("accepted_break", "rejected_break")
    side_names = ("long", "short")
    checks = {
        "deterministic_replay_verified": report["deterministic_replay"]["verified"],
        "causal_warmup_completed": (
            report["score_window"]["effective_start_session_after_causal_warmup"]
            is not None
        ),
        "zero_missing_expected_cme_sessions": (
            report["score_window"]["missing_expected_session_count"] == 0
        ),
        "zero_unexpected_closed_session_snapshots": (
            report["score_window"]["unexpected_snapshot_session_count"] == 0
        ),
        "at_least_300_trades": overall["trade_count"] >= 300,
        "trades_span_at_least_three_calendar_years": len(traded_years) >= 3,
        "at_least_50_trades_each_enabled_branch": all(
            branches.get(name, {}).get("survivability", {}).get("trade_count", 0) >= 50
            for name in branch_names
        ),
        "at_least_75_trades_each_traded_side": all(
            sides.get(name, {}).get("survivability", {}).get("trade_count", 0) >= 75
            for name in side_names
        ),
        "positive_net_pnl": overall["net_pnl"] > 0,
        "positive_expectancy": overall["expectancy_per_trade"] > 0,
        "profit_factor_at_least_1_25": _pf_pass(report["overall"], 1.25),
        "daily_sharpe_at_least_1_25": daily["sharpe_annualized"] >= 1.25,
        "average_weekly_r_at_least_0_50": (
            weekly["realized_net_r"]["mean"] is not None
            and weekly["realized_net_r"]["mean"] >= 0.50
        ),
        "average_weekly_dollars_positive": (
            weekly["net_pnl"]["mean"] is not None
            and weekly["net_pnl"]["mean"] > 0
        ),
        "bootstrap_nonpositive_below_5pct": (
            bootstrap["probability_total_net_nonpositive"] < 0.05
        ),
        "annualized_net_to_p95_drawdown_at_least_1": (
            annualized_net_to_p95_pass
        ),
        "at_least_70pct_complete_half_years_positive": (
            len(half_years) > 0 and positive_folds >= required_positive_folds
        ),
        "complete_half_year_coverage": (
            len(half_years) >= 6
            and all(fold["eligible_sessions"] >= 100 for fold in half_years.values())
        ),
        "final_chronological_half_year_positive": (
            final_fold is not None and final_fold["net_pnl_including_zero_days"] > 0
        ),
        "both_enabled_branches_positive_pf_at_least_1": all(
            name in branches
            and branches[name]["survivability"]["net_pnl"] > 0
            and _pf_pass(branches[name], 1.0)
            for name in branch_names
        ),
        "both_sides_positive_pf_at_least_1": all(
            name in sides
            and sides[name]["survivability"]["net_pnl"] > 0
            and _pf_pass(sides[name], 1.0)
            for name in side_names
        ),
        "positive_without_top_five_trades": overall["pnl_without_top_5_trades"] > 0,
        "positive_without_top_five_days": daily["pnl_without_top_5_days"] > 0,
        "top_five_day_share_no_more_than_35pct": (
            daily["top_5_day_share"] is not None and daily["top_5_day_share"] <= 0.35
        ),
        "bootstrap_p99_drawdown_disclosed": (
            bootstrap["max_drawdown_p99_adverse"] is not None
            and bootstrap["draws"] >= 20_000
        ),
        "zero_reconciliation_violations": (
            report["execution_diagnostics"]["reconciliation_violation_count"] == 0
        ),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "positive_complete_half_years": positive_folds,
        "required_positive_complete_half_years": required_positive_folds,
        "annualized_net_to_p95_drawdown": annualized_net_to_p95,
        "scope": (
            "T1 normal-cost primary only; cost/fill stress, robustness, capital-policy, "
            "and prospective gates remain separate"
        ),
    }


def build_opening_auction_retest_report(
    *,
    trades: Sequence[Trade],
    ledger: EventLedger,
    snapshots: Sequence[RetestSessionSnapshot],
    diagnostic_events: Sequence[RetestDiagnosticEvent],
    point_value: float,
    score_start_session: str,
    score_end_session_exclusive: str,
    candidate_family_trial_budget: int = 9,
    expected_entry_delay_bars: int = 0,
    allocated_capital: Optional[float] = None,
    hard_loss_limit: Optional[float] = None,
) -> dict[str, Any]:
    scored_trades = [
        trade
        for trade in trades
        if score_start_session <= trade.session_date < score_end_session_exclusive
    ]
    scored_snapshots = [
        snapshot
        for snapshot in snapshots
        if score_start_session <= snapshot.features.session_date < score_end_session_exclusive
    ]
    snapshot_by_session: dict[str, RetestSessionSnapshot] = {}
    for snapshot in scored_snapshots:
        session_day = snapshot.features.session_date
        if session_day in snapshot_by_session:
            raise ValueError(f"duplicate session snapshot: {session_day}")
        snapshot_by_session[session_day] = snapshot

    expected_from_requested_start = _expected_cme_sessions(
        score_start_session, score_end_session_exclusive
    )
    expected_set = set(expected_from_requested_start)
    missing_expected_sessions = sorted(expected_set - set(snapshot_by_session))
    unexpected_snapshot_sessions = sorted(set(snapshot_by_session) - expected_set)
    warmup_ready_sessions = [
        day
        for day in expected_from_requested_start
        if day in snapshot_by_session and _warmup_ready(snapshot_by_session[day])
    ]
    effective_score_start = warmup_ready_sessions[0] if warmup_ready_sessions else None
    session_dates = [
        day
        for day in expected_from_requested_start
        if effective_score_start is not None and day >= effective_score_start
    ]
    score_session_set = set(session_dates)
    score_snapshots = [
        snapshot_by_session[day]
        for day in session_dates
        if day in snapshot_by_session
    ]
    feature_ready_snapshots = [
        snapshot for snapshot in score_snapshots if _feature_ready(snapshot)
    ]
    feature_ready_session_set = {
        snapshot.features.session_date for snapshot in feature_ready_snapshots
    }
    ineligible_trade_days = {
        trade.session_date
        for trade in scored_trades
        if trade.session_date not in feature_ready_session_set
    }
    if ineligible_trade_days:
        raise ValueError(
            f"trades occurred outside feature-ready sessions: {sorted(ineligible_trade_days)}"
        )
    attributed = attribute_retest_trades(scored_trades, ledger)
    daily_pnl = {day: 0.0 for day in session_dates}
    realized_r_by_session = {day: 0.0 for day in session_dates}
    trade_days: set[str] = set()

    fill_relative_trades: list[dict[str, Any]] = []
    target_behind_fill = 0
    for item in attributed:
        trade = item.trade
        direction = 1.0 if trade.side == "long" else -1.0
        fill_risk = abs(trade.entry_price - item.entry.stop_price)
        fill_reward = (item.entry.target_price - trade.entry_price) * direction
        risk_dollars = fill_risk * point_value * trade.quantity
        realized_r = trade.net_pnl / risk_dollars if risk_dollars > 0 else None
        behind = max(0.0, -fill_reward)
        target_behind_fill += behind > 0
        row = {
            "entry_timestamp_utc": trade.entry_timestamp_utc,
            "session_date": trade.session_date,
            "branch": item.entry.branch,
            "side": trade.side,
            "signal_timestamp_utc": item.entry.signal_timestamp_utc,
            "signal_price": item.entry.signal_price,
            "fill_price": trade.entry_price,
            "stop_price": item.entry.stop_price,
            "target_price": item.entry.target_price,
            "reference_side": item.entry.reference_side,
            "reference_type": item.entry.reference_type,
            "reference_price": item.entry.reference_price,
            "adverse_entry_gap_points": (
                trade.entry_price - item.entry.signal_price
            )
            * direction,
            "fill_risk_points": fill_risk,
            "fill_target_reward_points": fill_reward,
            "fill_target_reward_r": fill_reward / fill_risk if fill_risk > 0 else None,
            "realized_net_r": realized_r,
            "target_behind_fill_points": behind,
        }
        fill_relative_trades.append(row)
        daily_pnl[trade.session_date] += trade.net_pnl
        if realized_r is not None:
            realized_r_by_session[trade.session_date] += realized_r
        trade_days.add(trade.session_date)

    daily_series = [daily_pnl[day] for day in session_dates]
    daily = build_daily_metrics(
        {day: daily_pnl[day] for day in trade_days}, session_dates
    ).to_dict()
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
    weekly = _weekly_report(
        session_dates=session_dates,
        daily_pnl=daily_pnl,
        realized_r_by_session=realized_r_by_session,
    )
    half_years = _complete_half_years(
        score_start_session=score_start_session,
        score_end_session_exclusive=score_end_session_exclusive,
        session_dates=session_dates,
        daily_pnl=daily_pnl,
        trades=scored_trades,
        point_value=point_value,
    )
    by_branch = _grouped(
        attributed, key=lambda item: item.entry.branch, point_value=point_value
    )
    by_side = _grouped(
        attributed, key=lambda item: item.trade.side, point_value=point_value
    )
    by_year = _grouped(
        attributed, key=lambda item: item.trade.session_date[:4], point_value=point_value
    )
    by_reference = _grouped(
        attributed,
        key=lambda item: item.entry.reference_type or "none",
        point_value=point_value,
    )
    by_weekday = _grouped(
        attributed,
        key=lambda item: date.fromisoformat(item.trade.session_date).strftime("%A"),
        point_value=point_value,
    )

    eligible_events = [
        event for event in diagnostic_events if event.session_date in score_session_set
    ]
    funnel = Counter(event.event for event in eligible_events)
    classifier_counts = Counter(
        f"{snapshot.classification.regime.value}:{snapshot.classification.side.value}"
        for snapshot in score_snapshots
    )
    classifier_reasons = Counter(
        snapshot.classification.reason for snapshot in score_snapshots
    )
    terminal_untraded = Counter()
    events_by_session: dict[str, list[RetestDiagnosticEvent]] = {}
    for event in eligible_events:
        events_by_session.setdefault(event.session_date, []).append(event)
    filled_sessions = {
        event.session_date for event in eligible_events if event.event == "filled"
    }
    for snapshot in feature_ready_snapshots:
        if snapshot.classification.regime.value == "no_trade":
            continue
        session_day = snapshot.features.session_date
        if session_day in filled_sessions:
            continue
        terminal = "classified_no_entry_event"
        for event in reversed(events_by_session.get(session_day, [])):
            if event.event in ("entry_cancelled", "entry_rejected"):
                terminal = str(event.metadata.get("reason", event.event))
                break
            if event.event == "entry_confirmed":
                terminal = "entry_confirmed_unfilled"
                break
            if event.event == "first_retest_armed":
                terminal = "armed_not_confirmed"
                break
        terminal_untraded[
            f"{snapshot.classification.regime.value}:{snapshot.classification.side.value}:{terminal}"
        ] += 1

    confirmations_by_session = Counter(
        event.session_date for event in eligible_events if event.event == "entry_confirmed"
    )
    fills_by_session = Counter(
        event.session_date for event in eligible_events if event.event == "filled"
    )
    trades_by_session = Counter(trade.session_date for trade in scored_trades)
    reconciliation = _audit_execution_reconciliation(
        ledger=ledger,
        trades=scored_trades,
        diagnostic_events=eligible_events,
        point_value=point_value,
        expected_entry_delay_bars=expected_entry_delay_bars,
        target_behind_fill_count=target_behind_fill,
    )
    violations = list(reconciliation["violations"])
    if any(count > 1 for count in confirmations_by_session.values()):
        violations.append("more_than_one_entry_confirmation_in_session")
    if any(count > 1 for count in fills_by_session.values()):
        violations.append("more_than_one_fill_in_session")
    if any(count > 1 for count in trades_by_session.values()):
        violations.append("more_than_one_closed_trade_in_session")
    reconciliation["violations"] = violations
    reconciliation["violation_count"] = len(violations)

    commission_drag = sum(item.trade.commission for item in attributed)
    slippage_drag = sum(
        float(record.payload.get("slippage_points", 0.0))
        * point_value
        * int(record.payload.get("quantity", 0))
        for record in ledger.records
        if record.event_type == EventType.FILL
    )
    overall = _summary(
        scored_trades,
        point_value=point_value,
        eligible_session_count=len(session_dates),
    )
    observed_net = float(overall["survivability"]["net_pnl"])
    observed_annualized_net = (
        observed_net * 252.0 / len(session_dates) if session_dates else 0.0
    )
    p99_drawdown_abs = abs(float(bootstrap["max_drawdown_p99_adverse"]))
    capital_gate_evaluated = allocated_capital is not None and hard_loss_limit is not None
    capital_limit = (
        min(0.25 * allocated_capital, 0.50 * hard_loss_limit)
        if capital_gate_evaluated
        else None
    )
    capital_gate_passed = (
        p99_drawdown_abs <= capital_limit
        if capital_limit is not None
        else None
    )

    report: dict[str, Any] = {
        "score_window": {
            "requested_start_session": score_start_session,
            "effective_start_session_after_causal_warmup": effective_score_start,
            "end_session_exclusive": score_end_session_exclusive,
            "expected_sessions_from_requested_start": len(expected_from_requested_start),
            "expected_score_sessions_after_warmup": len(session_dates),
            "classified_sessions_total_audit": len(scored_snapshots),
            "classified_score_sessions": len(score_snapshots),
            "gate_eligible_sessions": len(session_dates),
            "feature_ready_setup_sessions": len(feature_ready_snapshots),
            "fail_closed_or_missing_score_sessions": (
                len(session_dates) - len(feature_ready_snapshots)
            ),
            "warmup_expected_sessions": (
                sum(day < effective_score_start for day in expected_from_requested_start)
                if effective_score_start is not None
                else len(expected_from_requested_start)
            ),
            "missing_expected_sessions": missing_expected_sessions,
            "missing_expected_session_count": len(missing_expected_sessions),
            "unexpected_snapshot_sessions": unexpected_snapshot_sessions,
            "unexpected_snapshot_session_count": len(unexpected_snapshot_sessions),
        },
        "overall": overall,
        "by_branch": by_branch,
        "by_side": by_side,
        "by_year": by_year,
        "by_weekday": by_weekday,
        "by_reference_type": by_reference,
        "complete_half_years": half_years,
        "daily": daily,
        "weekly": weekly,
        "monthly": {
            month: {
                "net_pnl": sum(
                    daily_pnl[day] for day in session_dates if day[:7] == month
                ),
                "eligible_sessions": sum(day[:7] == month for day in session_dates),
                "trade_days": sum(
                    day[:7] == month and day in trade_days for day in session_dates
                ),
            }
            for month in sorted({day[:7] for day in session_dates})
        },
        "bootstrap": bootstrap,
        "statistical_confidence": confidence,
        "allocated_capital_returns": (
            {
                "status": "available",
                "daily_return_fraction": _distribution(
                    daily_pnl[day] / allocated_capital for day in session_dates
                ),
                "weekly_return_fraction": _distribution(
                    row["net_pnl"] / allocated_capital for row in weekly["weeks"]
                ),
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
                "evaluated": capital_gate_evaluated,
                "passed": capital_gate_passed,
            },
        },
        "classifier_counts": dict(sorted(classifier_counts.items())),
        "classifier_reasons": dict(sorted(classifier_reasons.items())),
        "funnel": dict(sorted(funnel.items())),
        "classified_but_untraded": dict(sorted(terminal_untraded.items())),
        "feature_distributions": {
            "dtr20_points": _distribution(
                float(snapshot.features.dtr20) for snapshot in feature_ready_snapshots
            ),
            "opening_volume_ratio": _distribution(
                float(snapshot.features.opening_volume_ratio)
                for snapshot in feature_ready_snapshots
                if snapshot.features.opening_volume_ratio is not None
            ),
            "opening_width_dtr": _distribution(
                float(snapshot.features.opening_width) / float(snapshot.features.dtr20)
                for snapshot in feature_ready_snapshots
                if snapshot.features.opening_width is not None
            ),
            "opening_displacement_dtr": _distribution(
                float(snapshot.features.displacement_dtr)
                for snapshot in feature_ready_snapshots
                if snapshot.features.displacement_dtr is not None
            ),
            "opening_efficiency": _distribution(
                float(snapshot.features.efficiency_ratio)
                for snapshot in feature_ready_snapshots
                if snapshot.features.efficiency_ratio is not None
            ),
            "opening_close_location": _distribution(
                float(snapshot.features.close_location)
                for snapshot in feature_ready_snapshots
                if snapshot.features.close_location is not None
            ),
        },
        "fill_relative_trades": fill_relative_trades,
        "execution_diagnostics": {
            "target_behind_fill_count": target_behind_fill,
            "entry_invalidated_at_fill_count": reconciliation[
                "entry_invalidated_at_fill_count"
            ],
            "ambiguous_exit_count": sum(trade.ambiguous_exit for trade in scored_trades),
            "modeled_commission_drag_dollars": commission_drag,
            "modeled_slippage_drag_dollars": slippage_drag,
            "adverse_entry_gap_points": _distribution(
                row["adverse_entry_gap_points"] for row in fill_relative_trades
            ),
            "fill_risk_points": _distribution(
                row["fill_risk_points"] for row in fill_relative_trades
            ),
            "fill_target_reward_r": _distribution(
                row["fill_target_reward_r"]
                for row in fill_relative_trades
                if row["fill_target_reward_r"] is not None
            ),
            "realized_net_r": _distribution(
                row["realized_net_r"]
                for row in fill_relative_trades
                if row["realized_net_r"] is not None
            ),
            "reconciliation_violations": violations,
            "reconciliation_violation_count": len(violations),
            "reconciliation": reconciliation,
        },
        "deterministic_replay": {
            "verified": False,
            "core_hashes": {},
            "mismatches": ["second_replay_not_supplied"],
        },
    }
    report["t1_primary_gates"] = evaluate_retest_t1_primary_gates(report)
    report["promotion_status"] = (
        "primary_historical_pass_cost_and_robustness_pending"
        if report["t1_primary_gates"]["passed"]
        else "rejected_primary_no_threshold_rescue"
    )
    return report
