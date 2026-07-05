"""Gate 1 Phase 4 sweep harness.

Runs a pre-registered grid of AdaptiveTrendConfig overrides on the train
window and scores each cell against the mechanically-computable rows of
the Phase 0 promotion table
(docs/decisions/2026-07-05-gate1-phase0-protocol.md). Design:
docs/superpowers/specs/2026-07-05-sweep-harness-design.md.

Row 8 (slippage sensitivity) is deliberately absent: it runs only for a
selected qualifier, before holdout. This module never touches holdout.

Row 9 is a session-level PAIRED t-test on per-session net P&L
differences (cell minus baseline over the union of active sessions,
absent session = 0). An unpaired Welch t between the two trade lists
would treat heavily-overlapping populations as independent samples --
the error class documented in feedback_mc_comparison_rules and flagged
in the prior-vol evaluation.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Sequence

from full_python.models import Trade

MATERIALITY_DOLLARS = 10_000.0
EXPECTANCY_MIN_IMPROVEMENT = 0.10
TRADE_COUNT_FLAG_DROP = 0.20
DRAWDOWN_MAX_WORSENING = 0.15
PAIRED_T_THRESHOLD = 2.0
OUTLIER_CUTS = (1, 2, 3)
MIN_BETTER_OR_NEUTRAL_YEARS = 2


@dataclass(frozen=True)
class CellResult:
    """One grid cell's train-window outcome. overrides == {} is the baseline."""

    overrides: dict
    trades: tuple[Trade, ...]
    error: Optional[str] = None
    config_hash: Optional[str] = None


@dataclass(frozen=True)
class CellScore:
    overrides: dict
    trade_count: int
    net_pnl: float
    delta_vs_baseline: float
    rows: dict
    passes_all: bool


def _net(trades: Sequence[Trade]) -> float:
    return sum(t.net_pnl for t in trades)


def _max_drawdown(trades: Sequence[Trade]) -> float:
    """Worst peak-to-trough of running equity over the trade sequence. <= 0."""
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for trade in trades:
        equity += trade.net_pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return max_dd


def _net_without_top(trades: Sequence[Trade], n: int) -> float:
    """Net P&L after removing the n largest-net trades from this population."""
    ordered = sorted((t.net_pnl for t in trades), reverse=True)
    return sum(ordered) - sum(ordered[:n])


def _paired_session_t(
    cell_trades: Sequence[Trade], baseline_trades: Sequence[Trade]
) -> tuple[Optional[float], int]:
    """Paired t on per-session net P&L differences (cell - baseline).

    Sessions are the union of session_dates where either population has a
    trade; a session absent from one side contributes 0.0 for that side.
    Returns (t, n_sessions); t is None when n < 2 or the differences have
    zero variance (no detectable difference -- the row fails, correctly).
    """
    cell_by: dict[str, float] = defaultdict(float)
    base_by: dict[str, float] = defaultdict(float)
    for trade in cell_trades:
        cell_by[trade.session_date] += trade.net_pnl
    for trade in baseline_trades:
        base_by[trade.session_date] += trade.net_pnl
    sessions = sorted(set(cell_by) | set(base_by))
    n = len(sessions)
    if n < 2:
        return None, n
    diffs = [cell_by.get(s, 0.0) - base_by.get(s, 0.0) for s in sessions]
    mean = sum(diffs) / n
    variance = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    if variance == 0.0:
        return None, n
    return mean / math.sqrt(variance / n), n
