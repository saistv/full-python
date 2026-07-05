"""Decomposed parity checks between a Python simulation and TradingView.

Upgrades "within 3% on exits" from an aggregate claim into a verified,
per-dimension property: a matching aggregate can hide a structural
mismatch on individual trades if it is never broken apart. See
docs/decisions/2026-07-04-parity-delta-report.md for the rendered report
on the frozen baseline window.

Note on "exact match required on exit reason" (Gate 3 of the migration
plan): TV's exit_signal strings ("Stop Loss", "ATF Flip") and the sim's
exit_reason strings ("stop", "atf_flip") differ in spelling by
construction -- they come from two different systems with two different
label vocabularies. This module does NOT normalize them into a shared
vocabulary (that would hide a real relabeling bug behind a lookup table).
It reports the raw exact-match count on the raw strings, so a caller
doing the Gate 3 golden-trade check can apply an explicit, reviewed
mapping if one is warranted -- not an implicit one buried here.

Note on the exit_reason normalization used below: it applies a documented
lowercase/spaces-to-underscores normalization ("Stop Loss" -> "stop_loss")
purely so the loose spelling difference doesn't drown out real mismatches
-- but "stop_loss" still won't equal the sim's "stop", so that pair
legitimately reports as inexact. Do not weaken the normalization further
to force a match.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from full_python.reconcile import ReconciliationReport


@dataclass(frozen=True)
class ParityDeltaReport:
    trade_count_exact: bool
    tv_trade_count: int
    sim_trade_count: int
    matched_count: int
    entry_timestamp_exact_count: int
    entry_price_exact_count: int
    exit_timestamp_exact_count: int
    exit_reason_exact_count: int
    max_abs_exit_price_delta: float | None
    largest_pnl_deltas: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_parity_delta_report(
    report: ReconciliationReport, *, largest_n: int = 20
) -> ParityDeltaReport:
    matches = report.matches
    entry_timestamp_exact = sum(
        1 for m in matches if m.get("entry_time_delta_minutes", 1.0) == 0.0
    )
    entry_price_exact = sum(1 for m in matches if m.get("entry_price_delta", 1.0) == 0.0)
    exit_timestamp_exact = sum(
        1 for m in matches if m.get("exit_time_delta_minutes") == 0.0
    )
    exit_reason_exact = sum(
        1
        for m in matches
        if m.get("tv_exit_signal", "").strip().lower().replace(" ", "_")
        == m.get("sim_exit_reason", "")
    )
    exit_deltas = [
        abs(m["exit_price_delta"]) for m in matches if m.get("exit_price_delta") is not None
    ]
    pnl_deltas = sorted(
        (m for m in matches if m.get("net_pnl_delta") is not None),
        key=lambda m: abs(m["net_pnl_delta"]),
        reverse=True,
    )[:largest_n]

    return ParityDeltaReport(
        trade_count_exact=(
            report.tv_trade_count == report.sim_trade_count == report.matched_count
        ),
        tv_trade_count=report.tv_trade_count,
        sim_trade_count=report.sim_trade_count,
        matched_count=report.matched_count,
        entry_timestamp_exact_count=entry_timestamp_exact,
        entry_price_exact_count=entry_price_exact,
        exit_timestamp_exact_count=exit_timestamp_exact,
        exit_reason_exact_count=exit_reason_exact,
        max_abs_exit_price_delta=max(exit_deltas) if exit_deltas else None,
        largest_pnl_deltas=pnl_deltas,
    )
