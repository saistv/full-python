"""Candidate-specific attribution and diagnostics for Opening Auction v1."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
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
    build_monthly_breakdown,
    build_survivability_report,
)
from full_python.strategy.opening_auction_regime import (
    AuctionDiagnosticEvent,
    AuctionSessionSnapshot,
    CONTINUATION_REASON,
    FAILED_AUCTION_REASON,
)


ENTRY_REASONS = (CONTINUATION_REASON, FAILED_AUCTION_REASON)


@dataclass(frozen=True)
class EntryAttribution:
    entry_timestamp_utc: str
    symbol: str
    side: str
    entry_reason: str
    branch: str
    signal_timestamp_utc: str
    signal_price: float
    stop_price: float
    target_price: float
    reference_type: Optional[str]


@dataclass(frozen=True)
class AttributedTrade:
    trade: Trade
    entry: EntryAttribution


def _entry_attributions(ledger: EventLedger) -> list[EntryAttribution]:
    pending: Optional[dict[str, Any]] = None
    result: list[EntryAttribution] = []
    for record in ledger.records:
        payload = record.payload
        if (
            record.event_type == EventType.ORDER_INTENT
            and payload.get("reason") in ENTRY_REASONS
        ):
            pending = {
                "timestamp": record.timestamp_utc,
                "symbol": str(payload["symbol"]),
                "side": str(payload["side"]),
                "reason": str(payload["reason"]),
                "signal_price": float(payload["signal_price"]),
                "stop_price": float(payload["stop_price"]),
                "target_price": float(payload["target_price"]),
                "reference_type": payload.get("reference_type"),
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
        if (
            record.event_type == EventType.FILL
            and payload.get("reason") in ENTRY_REASONS
        ):
            if pending is None:
                raise ValueError("entry fill has no attributable order intent")
            fill_side = "long" if payload["side"] == "buy" else "short"
            intended_side = "long" if pending["side"] == "buy" else "short"
            if fill_side != intended_side or payload["symbol"] != pending["symbol"]:
                raise ValueError("entry fill does not match pending order intent")
            reason = str(payload["reason"])
            result.append(
                EntryAttribution(
                    entry_timestamp_utc=record.timestamp_utc,
                    symbol=str(payload["symbol"]),
                    side=fill_side,
                    entry_reason=reason,
                    branch=(
                        "initiative" if reason == CONTINUATION_REASON else "failed_auction"
                    ),
                    signal_timestamp_utc=str(pending["timestamp"]),
                    signal_price=float(pending["signal_price"]),
                    stop_price=float(pending["stop_price"]),
                    target_price=float(pending["target_price"]),
                    reference_type=(
                        None
                        if pending["reference_type"] is None
                        else str(pending["reference_type"])
                    ),
                )
            )
            pending = None
    return result


def attribute_trades(
    trades: Sequence[Trade], ledger: EventLedger
) -> tuple[AttributedTrade, ...]:
    entries: dict[tuple[str, str, str], EntryAttribution] = {}
    for entry in _entry_attributions(ledger):
        key = (entry.entry_timestamp_utc, entry.symbol, entry.side)
        if key in entries:
            raise ValueError(f"duplicate entry attribution: {key}")
        entries[key] = entry

    attributed = []
    for trade in trades:
        key = (trade.entry_timestamp_utc, trade.symbol, trade.side)
        entry = entries.pop(key, None)
        if entry is None:
            raise ValueError(f"trade has no entry attribution: {key}")
        attributed.append(AttributedTrade(trade=trade, entry=entry))
    if entries:
        raise ValueError("filled auction entries did not produce closed trades")
    return tuple(attributed)


def _t_stat(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    deviation = statistics.stdev(values)
    if deviation == 0:
        return None
    return statistics.mean(values) / (deviation / math.sqrt(len(values)))


def _summary(trades: Sequence[Trade], *, point_value: float) -> dict[str, Any]:
    survivability = build_survivability_report(
        [TradeResult(t.exit_timestamp_utc, t.side, t.net_pnl) for t in trades]
    )
    metrics = build_metrics_report(trades, point_value=point_value)
    risks = [abs(trade.entry_price - trade.stop_price) for trade in trades]
    mfe_r = [
        trade.mfe_points / risk for trade, risk in zip(trades, risks) if risk > 0
    ]
    mae_r = [
        trade.mae_points / risk for trade, risk in zip(trades, risks) if risk > 0
    ]
    return {
        "survivability": survivability.to_dict(),
        "metrics": metrics.to_dict(),
        "trade_t_stat": _t_stat([trade.net_pnl for trade in trades]),
        "excursions": {
            "mean_mfe_points": (
                statistics.mean(trade.mfe_points for trade in trades) if trades else None
            ),
            "median_mfe_points": (
                statistics.median(trade.mfe_points for trade in trades) if trades else None
            ),
            "mean_mae_points": (
                statistics.mean(trade.mae_points for trade in trades) if trades else None
            ),
            "median_mae_points": (
                statistics.median(trade.mae_points for trade in trades) if trades else None
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
        "p10": _quantile(finite, 0.10),
        "p25": _quantile(finite, 0.25),
        "median": _quantile(finite, 0.50),
        "mean": statistics.mean(finite) if finite else None,
        "p75": _quantile(finite, 0.75),
        "p90": _quantile(finite, 0.90),
        "max": max(finite, default=None),
    }


def _feature_ready(snapshot: AuctionSessionSnapshot) -> bool:
    features = snapshot.features
    required = (
        features.dtr20,
        features.opening_volume_ratio,
        features.rth_open,
        features.opening_high,
        features.opening_low,
        features.opening_close,
        features.opening_width,
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
        and all(value is not None and math.isfinite(float(value)) for value in required)
        and float(features.dtr20) > 0
        and float(features.opening_width) > 0
    )


def _grouped(
    attributed: Sequence[AttributedTrade],
    *,
    key,
    point_value: float,
) -> dict[str, Any]:
    buckets: dict[str, list[Trade]] = {}
    for item in attributed:
        buckets.setdefault(str(key(item)), []).append(item.trade)
    return {
        name: _summary(group, point_value=point_value)
        for name, group in sorted(buckets.items())
    }


def _profit_factor_pass(value: Optional[float], threshold: float) -> bool:
    return value is None or value >= threshold


def evaluate_t1_primary_gates(report: dict[str, Any]) -> dict[str, Any]:
    overall = report["overall"]["survivability"]
    branches = report["by_branch"]
    sides = report["by_side"]
    daily = report["daily"]
    bootstrap = report["bootstrap"]
    years = report["by_year"]
    p95_drawdown = float(bootstrap["max_drawdown_p95_adverse"])
    net_to_p95 = (
        float(overall["net_pnl"]) / abs(p95_drawdown)
        if p95_drawdown < 0
        else None
    )

    branch_names = ("initiative", "failed_auction")
    side_names = ("long", "short")
    checks = {
        "at_least_100_trades": overall["trade_count"] >= 100,
        "at_least_50_trades_each_branch": all(
            branches.get(name, {}).get("survivability", {}).get("trade_count", 0) >= 50
            for name in branch_names
        ),
        "net_at_least_20000": overall["net_pnl"] >= 20_000.0,
        "profit_factor_at_least_1_25": _profit_factor_pass(
            overall["profit_factor"], 1.25
        ),
        "positive_expectancy": overall["expectancy_per_trade"] > 0,
        "session_t_at_least_2": (
            report["session_t_stat"] is not None and report["session_t_stat"] >= 2.0
        ),
        "bootstrap_nonpositive_no_more_than_5pct": (
            bootstrap["probability_total_net_nonpositive"] <= 0.05
        ),
        "net_to_p95_drawdown_at_least_1_5": (
            net_to_p95 is not None and net_to_p95 >= 1.5
        ),
        "both_branches_positive_pf_at_least_1": all(
            name in branches
            and branches[name]["survivability"]["net_pnl"] > 0
            and _profit_factor_pass(
                branches[name]["survivability"]["profit_factor"], 1.0
            )
            for name in branch_names
        ),
        "both_sides_positive": all(
            name in sides and sides[name]["survivability"]["net_pnl"] > 0
            for name in side_names
        ),
        "positive_without_top_trades": all(
            overall[field] > 0
            for field in (
                "pnl_without_best_trade",
                "pnl_without_top_3_trades",
                "pnl_without_top_5_trades",
            )
        ),
        "positive_without_top_days": all(
            daily[field] > 0
            for field in (
                "pnl_without_top_1_day",
                "pnl_without_top_3_days",
                "pnl_without_top_5_days",
            )
        ),
        "top_5_day_share_no_more_than_40pct": (
            daily["top_5_day_share"] is not None and daily["top_5_day_share"] <= 0.40
        ),
        "at_least_three_positive_calendar_cohorts": sum(
            bucket["survivability"]["net_pnl"] > 0 for bucket in years.values()
        )
        >= 3,
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "net_to_p95_drawdown": net_to_p95,
        "scope": "T1 primary only; robustness and higher-cost gates are not yet evaluated",
    }


def build_opening_auction_report(
    *,
    trades: Sequence[Trade],
    ledger: EventLedger,
    snapshots: Sequence[AuctionSessionSnapshot],
    diagnostic_events: Sequence[AuctionDiagnosticEvent],
    point_value: float,
    score_start_session: str,
    score_end_session_exclusive: str,
) -> dict[str, Any]:
    scored_trades = [
        trade
        for trade in trades
        if score_start_session <= trade.session_date < score_end_session_exclusive
    ]
    attributed = attribute_trades(scored_trades, ledger)
    scored_snapshots = [
        snapshot
        for snapshot in snapshots
        if score_start_session
        <= snapshot.features.session_date
        < score_end_session_exclusive
    ]
    eligible_snapshots = [item for item in scored_snapshots if _feature_ready(item)]
    eligible_session_set = {
        snapshot.features.session_date for snapshot in eligible_snapshots
    }
    scored_events = [
        event
        for event in diagnostic_events
        if event.session_date in eligible_session_set
    ]
    ineligible_trade_days = {
        trade.session_date for trade in scored_trades if trade.session_date not in eligible_session_set
    }
    if ineligible_trade_days:
        raise ValueError(
            f"trades occurred outside feature-ready sessions: {sorted(ineligible_trade_days)}"
        )

    session_dates = sorted(eligible_session_set)
    daily_pnl = {day: 0.0 for day in session_dates}
    trade_days: set[str] = set()
    for trade in scored_trades:
        daily_pnl[trade.session_date] = daily_pnl.get(trade.session_date, 0.0) + trade.net_pnl
        trade_days.add(trade.session_date)
    daily_series = [daily_pnl[day] for day in session_dates]
    daily_metrics = build_daily_metrics(
        {day: daily_pnl[day] for day in trade_days}, session_dates
    )
    bootstrap = build_block_bootstrap_report(daily_series)

    classifier = Counter(
        f"{item.classification.regime.value}:{item.classification.side.value}"
        for item in eligible_snapshots
    )
    classifier_all = Counter(
        f"{item.classification.regime.value}:{item.classification.side.value}"
        for item in scored_snapshots
    )
    classifier_reasons = Counter(item.classification.reason for item in eligible_snapshots)
    classifier_reasons_all = Counter(item.classification.reason for item in scored_snapshots)
    funnel = Counter(item.event for item in scored_events)
    events_by_session: dict[str, list[AuctionDiagnosticEvent]] = {}
    for event in scored_events:
        events_by_session.setdefault(event.session_date, []).append(event)
    filled_sessions = {
        event.session_date for event in scored_events if event.event == "filled"
    }
    classified_but_untraded = Counter()
    for snapshot in eligible_snapshots:
        classification = snapshot.classification
        session_day = snapshot.features.session_date
        if classification.regime.value == "no_trade" or session_day in filled_sessions:
            continue
        events = events_by_session.get(session_day, [])
        terminal = "classified_no_entry_event"
        for event in reversed(events):
            if event.event == "entry_rejected":
                terminal = str(event.metadata.get("reason", "entry_rejected"))
                break
            if event.event == "continuation_cancelled":
                terminal = str(event.metadata.get("reason", "continuation_cancelled"))
                break
            if event.event == "entry_confirmed":
                terminal = "entry_confirmed_unfilled"
                break
            if event.event == "continuation_armed":
                terminal = "armed_not_confirmed"
                break
        classified_but_untraded[
            f"{classification.regime.value}:{classification.side.value}:{terminal}"
        ] += 1

    dtr_values = [float(item.features.dtr20) for item in eligible_snapshots]
    volume_ratios = [
        float(item.features.opening_volume_ratio) for item in eligible_snapshots
    ]
    or_width_dtr = [
        float(item.features.opening_width) / float(item.features.dtr20)
        for item in eligible_snapshots
    ]
    gap_dtr = [
        (float(item.features.rth_open) - float(item.features.prior_rth_close))
        / float(item.features.dtr20)
        for item in eligible_snapshots
    ]
    overnight_gaps = [
        float(item.features.overnight_max_gap_minutes)
        for item in eligible_snapshots
        if item.features.overnight_max_gap_minutes is not None
    ]
    target_behind_fill = sum(
        (
            item.trade.side == "long"
            and item.entry.target_price <= item.trade.entry_price
        )
        or (
            item.trade.side == "short"
            and item.entry.target_price >= item.trade.entry_price
        )
        for item in attributed
    )
    fill_relative_trades = []
    for item in attributed:
        direction = 1.0 if item.trade.side == "long" else -1.0
        fill_risk = abs(item.trade.entry_price - item.entry.stop_price)
        fill_reward = (item.entry.target_price - item.trade.entry_price) * direction
        risk_dollars = fill_risk * point_value * item.trade.quantity
        behind_points = max(0.0, -fill_reward)
        fill_relative_trades.append(
            {
                "entry_timestamp_utc": item.trade.entry_timestamp_utc,
                "session_date": item.trade.session_date,
                "branch": item.entry.branch,
                "side": item.trade.side,
                "signal_price": item.entry.signal_price,
                "fill_price": item.trade.entry_price,
                "stop_price": item.entry.stop_price,
                "target_price": item.entry.target_price,
                "adverse_entry_gap_points": (
                    item.trade.entry_price - item.entry.signal_price
                )
                * direction,
                "fill_risk_points": fill_risk,
                "fill_target_reward_points": fill_reward,
                "fill_target_reward_r": (
                    fill_reward / fill_risk if fill_risk > 0 else None
                ),
                "realized_net_r": (
                    item.trade.net_pnl / risk_dollars if risk_dollars > 0 else None
                ),
                "target_behind_fill_points": behind_points,
            }
        )
    entry_gap_points = [
        row["adverse_entry_gap_points"] for row in fill_relative_trades
    ]
    fill_risks = [row["fill_risk_points"] for row in fill_relative_trades]
    fill_target_rs = [
        row["fill_target_reward_r"]
        for row in fill_relative_trades
        if row["fill_target_reward_r"] is not None
    ]
    realized_rs = [
        row["realized_net_r"]
        for row in fill_relative_trades
        if row["realized_net_r"] is not None
    ]
    target_behind_points = [
        row["target_behind_fill_points"] for row in fill_relative_trades
    ]
    commission_drag = sum(item.trade.commission for item in attributed)
    slippage_drag = sum(
        float(record.payload.get("slippage_points", 0.0))
        * point_value
        * int(record.payload.get("quantity", 0))
        for record in ledger.records
        if record.event_type == EventType.FILL
    )
    abbreviated_sessions = {
        session_day
        for session_day in eligible_session_set
        if (
            rth_close_minutes_et(date.fromisoformat(session_day)) is not None
            and int(rth_close_minutes_et(date.fromisoformat(session_day))) < 16 * 60
        )
    }
    abbreviated_trades = [
        trade for trade in scored_trades if trade.session_date in abbreviated_sessions
    ]
    regular_trades = [
        trade for trade in scored_trades if trade.session_date not in abbreviated_sessions
    ]

    report: dict[str, Any] = {
        "score_window": {
            "start_session": score_start_session,
            "end_session_exclusive": score_end_session_exclusive,
            "classified_sessions_total_audit": len(scored_snapshots),
            "gate_eligible_sessions": len(eligible_snapshots),
            "warmup_or_data_ineligible_sessions": (
                len(scored_snapshots) - len(eligible_snapshots)
            ),
        },
        "overall": _summary(scored_trades, point_value=point_value),
        "by_branch": _grouped(
            attributed, key=lambda item: item.entry.branch, point_value=point_value
        ),
        "by_side": _grouped(
            attributed, key=lambda item: item.trade.side, point_value=point_value
        ),
        "by_year": _grouped(
            attributed,
            key=lambda item: item.trade.session_date[:4],
            point_value=point_value,
        ),
        "by_half_year": _grouped(
            attributed,
            key=lambda item: (
                f"{item.trade.session_date[:4]}-H"
                f"{1 if int(item.trade.session_date[5:7]) <= 6 else 2}"
            ),
            point_value=point_value,
        ),
        "by_reference_type": _grouped(
            attributed,
            key=lambda item: item.entry.reference_type or "none",
            point_value=point_value,
        ),
        "daily": daily_metrics.to_dict(),
        "monthly": build_monthly_breakdown({day: daily_pnl[day] for day in trade_days}),
        "bootstrap": bootstrap.to_dict(),
        "session_t_stat": _t_stat(daily_series),
        "classifier_counts": dict(sorted(classifier.items())),
        "classifier_counts_all_audit": dict(sorted(classifier_all.items())),
        "classifier_reasons": dict(sorted(classifier_reasons.items())),
        "classifier_reasons_all_audit": dict(sorted(classifier_reasons_all.items())),
        "funnel": dict(sorted(funnel.items())),
        "classified_but_untraded": dict(sorted(classified_but_untraded.items())),
        "feature_distributions": {
            "dtr20_points": _distribution(dtr_values),
            "opening_volume_ratio": _distribution(volume_ratios),
            "opening_width_dtr": _distribution(or_width_dtr),
            "rth_gap_dtr": _distribution(gap_dtr),
            "overnight_max_gap_minutes": _distribution(overnight_gaps),
        },
        "calendar_attribution": {
            "continuous_contract_roll_no_trade_sessions": sum(
                item.classification.reason == "continuous_contract_roll"
                for item in scored_snapshots
            ),
            "incomplete_overnight_no_trade_sessions": sum(
                item.classification.reason == "incomplete_overnight_coverage"
                for item in scored_snapshots
            ),
            "abbreviated_sessions": len(abbreviated_sessions),
            "abbreviated_session_trades": _summary(
                abbreviated_trades, point_value=point_value
            ),
            "regular_session_trades": _summary(
                regular_trades, point_value=point_value
            ),
        },
        "execution_diagnostics": {
            "target_behind_fill_count": target_behind_fill,
            "max_target_behind_fill_points": max(target_behind_points, default=0.0),
            "mean_adverse_entry_gap_points": (
                statistics.mean(entry_gap_points) if entry_gap_points else None
            ),
            "max_adverse_entry_gap_points": max(entry_gap_points, default=None),
            "mean_fill_risk_points": (
                statistics.mean(fill_risks) if fill_risks else None
            ),
            "median_fill_target_reward_r": (
                statistics.median(fill_target_rs) if fill_target_rs else None
            ),
            "mean_realized_net_r": (
                statistics.mean(realized_rs) if realized_rs else None
            ),
            "median_realized_net_r": (
                statistics.median(realized_rs) if realized_rs else None
            ),
            "commission_drag_dollars": commission_drag,
            "modeled_slippage_drag_dollars": slippage_drag,
            "total_modeled_cost_drag_dollars": commission_drag + slippage_drag,
        },
        "fill_relative_trades": fill_relative_trades,
    }
    report["t1_primary_gates"] = evaluate_t1_primary_gates(report)
    return report
