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

from full_python.models import MarketBar, Trade
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import AdaptiveTrendConfig

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


def _year_nets(trades: Sequence[Trade]) -> dict[str, float]:
    nets: dict[str, float] = defaultdict(float)
    for trade in trades:
        nets[trade.entry_timestamp_utc[:4]] += trade.net_pnl
    return dict(nets)


def _side_net(trades: Sequence[Trade], side: str) -> float:
    return sum(t.net_pnl for t in trades if t.side == side)


def score_cell(cell: CellResult, baseline: CellResult) -> CellScore:
    """Score one cell against the baseline on the train promotion rows.

    Rows 1-7 and 9 of the Phase 0 promotion table; row 3 (trade count) is
    flag-only per the spec -- justification of a count drop is a human
    judgment, the harness only reports it. Row 8 (slippage) is deferred
    to the selected qualifier and is not scored here.
    """
    cell_net = _net(cell.trades)
    base_net = _net(baseline.trades)
    delta = cell_net - base_net
    rows: dict[str, dict] = {}

    rows["materiality"] = {"pass": delta >= MATERIALITY_DOLLARS, "delta": delta}

    if cell.trades and baseline.trades:
        cell_exp = cell_net / len(cell.trades)
        base_exp = base_net / len(baseline.trades)
        exp_pass = cell_exp >= base_exp + EXPECTANCY_MIN_IMPROVEMENT * abs(base_exp)
    else:
        cell_exp = None
        base_exp = None
        exp_pass = False
    rows["expectancy"] = {"pass": exp_pass, "baseline": base_exp, "cell": cell_exp}

    flagged = len(cell.trades) < (1.0 - TRADE_COUNT_FLAG_DROP) * len(baseline.trades)
    rows["trade_count"] = {
        "pass": True,  # flag-only: a drop needs human justification, not auto-fail
        "needs_justification": flagged,
        "baseline": len(baseline.trades),
        "cell": len(cell.trades),
    }

    cell_dd = _max_drawdown(cell.trades)
    base_dd = _max_drawdown(baseline.trades)
    rows["drawdown"] = {
        "pass": cell_dd >= base_dd * (1.0 + DRAWDOWN_MAX_WORSENING),
        "baseline": base_dd,
        "cell": cell_dd,
    }

    cuts = {
        n: (_net_without_top(cell.trades, n), _net_without_top(baseline.trades, n))
        for n in OUTLIER_CUTS
    }
    rows["outlier_survival"] = {
        "pass": all(c > b for c, b in cuts.values()),
        "cuts": {n: {"cell": c, "baseline": b} for n, (c, b) in cuts.items()},
    }

    base_years = _year_nets(baseline.trades)
    cell_years = _year_nets(cell.trades)
    better = sum(
        1 for year, base_val in base_years.items()
        if cell_years.get(year, 0.0) >= base_val
    )
    rows["year_by_year"] = {
        "pass": better >= MIN_BETTER_OR_NEUTRAL_YEARS,
        "better_or_neutral": better,
        "years": {
            year: {"baseline": base_val, "cell": cell_years.get(year, 0.0)}
            for year, base_val in sorted(base_years.items())
        },
    }

    long_delta = _side_net(cell.trades, "long") - _side_net(baseline.trades, "long")
    short_delta = _side_net(cell.trades, "short") - _side_net(baseline.trades, "short")
    rows["side_symmetry"] = {
        "pass": long_delta >= 0.0 and short_delta >= 0.0,
        "long_delta": long_delta,
        "short_delta": short_delta,
    }

    t_stat, n_sessions = _paired_session_t(cell.trades, baseline.trades)
    rows["paired_t"] = {
        "pass": t_stat is not None and abs(t_stat) >= PAIRED_T_THRESHOLD,
        "t": t_stat,
        "n_sessions": n_sessions,
    }

    return CellScore(
        overrides=dict(cell.overrides),
        trade_count=len(cell.trades),
        net_pnl=cell_net,
        delta_vs_baseline=delta,
        rows=rows,
        passes_all=all(row["pass"] for row in rows.values()),
    )


def select_qualifier(scores: Sequence[CellScore]) -> Optional[CellScore]:
    """The pre-registered selection rule: among cells passing ALL scored
    rows, the single best by net P&L -- and only that one -- may proceed
    to the slippage row and the one-shot holdout. The baseline cell
    (empty overrides) can never qualify against itself. Returns None when
    nothing qualifies, which closes the axis on train evidence alone.
    """
    qualifiers = [s for s in scores if s.passes_all and s.overrides != {}]
    if not qualifiers:
        return None
    return max(qualifiers, key=lambda s: s.net_pnl)


def run_grid(
    bars: Sequence[MarketBar],
    base_config: AdaptiveTrendConfig,
    overrides_list: Sequence[dict],
    sim_config: SimulationConfig,
    train_start: str,
    train_end: str,
) -> list[CellResult]:
    """Run every override dict through a fresh strategy + engine.

    The baseline cell is the empty dict and flows through the identical
    path, so baseline and cells cannot diverge in cost model or slicing.
    A raising cell is captured as CellResult.error and the grid
    continues; it is never silently dropped.
    """
    results: list[CellResult] = []
    for overrides in overrides_list:
        try:
            config = AdaptiveTrendConfig(**{**base_config.to_dict(), **overrides})
            strategy = AdaptiveTrendStrategy(config)
            outcome = SimulationEngine(sim_config).run(bars, strategy)
            trades = tuple(
                t for t in outcome.trades
                if train_start <= t.entry_timestamp_utc < train_end
            )
            results.append(CellResult(
                overrides=dict(overrides), trades=trades,
                config_hash=config.parameter_hash(),
            ))
        except Exception as exc:  # noqa: BLE001 -- cell isolation is the contract
            results.append(CellResult(
                overrides=dict(overrides), trades=(),
                error=f"{type(exc).__name__}: {exc}",
            ))
    return results
