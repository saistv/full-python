# Gate 1 Sweep Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tested `run grid → score cells → select qualifier` library (`src/full_python/research/sweep.py`) plus a thin driver (`scripts/sweep_ma_lengths.py`) that pre-registers the 5×5 MA-length grid and scores it against the Phase 0 promotion table.

**Architecture:** New `research/` package holding the reusable sweep core. Scoring rows are small pure helper functions, unit-tested directly on synthetic trades; `score_cell` aggregates them; `run_grid` drives `SimulationEngine` per override dict with the baseline as the empty-override cell flowing through the identical path; `select_qualifier` is the pre-registered one-best-cell-once rule as code. The driver only wires data + grid literals + output files.

**Tech Stack:** Python 3 stdlib only (dataclasses, csv, json, math, collections). Existing `full_python` modules: `models.Trade`, `simulation.SimulationEngine/SimulationConfig`, `strategy.adaptive_trend.AdaptiveTrendStrategy`, `strategy.adaptive_trend_config.AdaptiveTrendConfig/production_am_config`, `data.loaders`, `cli.TRADE_CSV_COLUMNS`, `scripts.freeze_baseline_anchor.FROZEN_SIMULATION_OVERRIDES`.

## Global Constraints

- No changes to `src/full_python/strategy/`, `src/full_python/simulation/`, `src/full_python/risk/`, `src/full_python/regime.py`, or `src/full_python/cli.py`. The harness only imports from them.
- The pre-registered grid is exactly `ma_50_length ∈ {30, 40, 50, 60, 70}` × `ma_200_length ∈ {100, 150, 200, 250, 300}` (25 cells, baseline (50, 200) as the empty override dict `{}`). No other values, no adaptive refinement.
- Cost model comes from `scripts.freeze_baseline_anchor.FROZEN_SIMULATION_OVERRIDES` — never re-typed literals.
- Promotion thresholds, verbatim from the spec: materiality ≥ +$10,000; expectancy improvement ≥ 10%; trade-count drop >20% is flag-only (never fails a cell); drawdown must not worsen >15%; outlier survival at top-1/2/3 cuts; ≥2 of 3 years better-or-neutral; long delta ≥ 0 AND short delta ≥ 0; session-level **paired** t with |t| ≥ 2.0 (NOT an unpaired Welch t between trade lists).
- The harness never runs holdout and never runs slippage variants (row 8) — those are follow-up steps outside this plan.
- `python3 -m pytest -q` must stay green after every task; tests are added, never removed. Baseline before Task 1: 137 passed, 2 skipped.
- Commits follow the existing style (`feat: ...`, `test: ...`).
- Tests use synthetic data only — no test reads `runs/` or invokes the 5-year CSV.

---

### Task 1: research package, dataclasses, and scoring helpers

**Files:**
- Create: `src/full_python/research/__init__.py`
- Create: `src/full_python/research/sweep.py`
- Create: `tests/test_sweep.py`

**Interfaces:**
- Consumes: `full_python.models.Trade` (fields: `net_pnl: float`, `side: str` ("long"/"short"), `entry_timestamp_utc: str` (ISO, year = first 4 chars), `session_date: str`).
- Produces (later tasks rely on these exact names):
  - `CellResult(overrides: dict, trades: tuple[Trade, ...], error: Optional[str] = None, config_hash: Optional[str] = None)` — frozen dataclass
  - `CellScore(overrides: dict, trade_count: int, net_pnl: float, delta_vs_baseline: float, rows: dict, passes_all: bool)` — frozen dataclass
  - `_net(trades) -> float`, `_max_drawdown(trades) -> float` (≤ 0), `_net_without_top(trades, n) -> float`, `_paired_session_t(cell_trades, baseline_trades) -> tuple[Optional[float], int]` (t or None, n_sessions)
  - Module constants: `MATERIALITY_DOLLARS = 10_000.0`, `EXPECTANCY_MIN_IMPROVEMENT = 0.10`, `TRADE_COUNT_FLAG_DROP = 0.20`, `DRAWDOWN_MAX_WORSENING = 0.15`, `PAIRED_T_THRESHOLD = 2.0`, `OUTLIER_CUTS = (1, 2, 3)`, `MIN_BETTER_OR_NEUTRAL_YEARS = 2`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sweep.py`:

```python
import pytest

from full_python.models import Trade
from full_python.research.sweep import (
    CellResult,
    _max_drawdown,
    _net,
    _net_without_top,
    _paired_session_t,
)


def _trade(net: float, side: str = "long", entry: str = "2023-05-01T14:31:00Z",
           session: str = "2023-05-01") -> Trade:
    return Trade(
        symbol="NQ", side=side, quantity=1,
        entry_timestamp_utc=entry, entry_price=100.0,
        exit_timestamp_utc=entry, exit_price=100.0,
        exit_reason="test", stop_price=99.0,
        gross_points=0.0, gross_pnl=net, commission=0.0, net_pnl=net,
        mfe_points=0.0, mae_points=0.0, session_date=session,
    )


def test_net_sums_net_pnl():
    trades = (_trade(500.0), _trade(-200.0), _trade(300.0))
    assert _net(trades) == 600.0
    assert _net(()) == 0.0


def test_max_drawdown_tracks_running_equity():
    # equity: 100, 50, -50, 150, 120 -> worst peak-to-trough = -50 -> -150
    trades = tuple(_trade(x) for x in (100.0, -50.0, -100.0, 200.0, -30.0))
    assert _max_drawdown(trades) == -150.0
    assert _max_drawdown(()) == 0.0
    # all-positive sequence never draws down
    assert _max_drawdown((_trade(10.0), _trade(20.0))) == 0.0


def test_net_without_top_removes_largest_winners():
    trades = tuple(_trade(x) for x in (500.0, -200.0, 400.0, -100.0, 300.0, 200.0))
    # net 1100; tops: 500, 400, 300
    assert _net_without_top(trades, 1) == 600.0
    assert _net_without_top(trades, 2) == 200.0
    assert _net_without_top(trades, 3) == -100.0


def test_paired_session_t_exact_value():
    # diffs per session: 50, 60, 40 -> mean 50, sample var 100, t = 5*sqrt(3)
    base = (
        _trade(100.0, session="2023-01-02"),
        _trade(200.0, session="2023-01-03"),
    )
    cell = (
        _trade(150.0, session="2023-01-02"),
        _trade(260.0, session="2023-01-03"),
        _trade(40.0, session="2023-01-04"),
    )
    t, n = _paired_session_t(cell, base)
    assert n == 3
    assert t == pytest.approx(8.6602540378, abs=1e-9)


def test_paired_session_t_boundary_two_point_zero():
    # diffs 10, 10, 40 -> mean 20, sample var 300, se 10, t exactly 2.0
    base = (
        _trade(100.0, session="2023-01-02"),
        _trade(200.0, session="2023-01-03"),
        _trade(300.0, session="2023-01-04"),
    )
    cell = (
        _trade(110.0, session="2023-01-02"),
        _trade(210.0, session="2023-01-03"),
        _trade(340.0, session="2023-01-04"),
    )
    t, n = _paired_session_t(cell, base)
    assert n == 3
    assert t == pytest.approx(2.0, abs=1e-12)


def test_paired_session_t_degenerate_cases_return_none():
    # single session -> None
    t, n = _paired_session_t((_trade(100.0, session="2023-01-02"),),
                             (_trade(50.0, session="2023-01-02"),))
    assert t is None and n == 1
    # identical populations -> zero variance -> None
    same = (_trade(100.0, session="2023-01-02"), _trade(200.0, session="2023-01-03"))
    t, n = _paired_session_t(same, same)
    assert t is None and n == 2


def test_cell_result_defaults():
    cell = CellResult(overrides={"ma_50_length": 40}, trades=())
    assert cell.error is None
    assert cell.config_hash is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sweep.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'full_python.research'`

- [ ] **Step 3: Write the implementation**

Create `src/full_python/research/__init__.py`:

```python
"""Research tooling (Gate 1 sweeps). Never imported by the production replay path."""
```

Create `src/full_python/research/sweep.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_sweep.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 144 passed, 2 skipped (137 baseline + 7 new)

- [ ] **Step 6: Commit**

```bash
git add src/full_python/research/ tests/test_sweep.py
git commit -m "feat: sweep harness scaffolding -- cell dataclasses and scoring helpers"
```

---

### Task 2: score_cell

**Files:**
- Modify: `src/full_python/research/sweep.py` (append after `_paired_session_t`)
- Modify: `tests/test_sweep.py` (append)

**Interfaces:**
- Consumes: everything Task 1 produced (`CellResult`, `CellScore`, all helpers and constants — exact names above).
- Produces: `score_cell(cell: CellResult, baseline: CellResult) -> CellScore`. The `rows` dict has exactly these keys: `"materiality"`, `"expectancy"`, `"trade_count"`, `"drawdown"`, `"outlier_survival"`, `"year_by_year"`, `"side_symmetry"`, `"paired_t"` — each mapping to a dict that contains at least `"pass": bool`. `passes_all` is True iff every row's `"pass"` is True (the `trade_count` row's `"pass"` is always True; it carries `"needs_justification"` as a flag instead).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sweep.py`:

```python
from full_python.research.sweep import CellScore, score_cell


# ---------------------------------------------------------------------------
# All-pass fixture. Baseline: 4 trades/year x 3 years (2023-2025), each on
# its own session. Cell: the same 12 trades (same sessions -> paired diff 0)
# plus 2 extra winners/year on new sessions.
#
# Hand-computed: baseline net 3000 (1000/year), cell net 30000, delta 27000.
# Paired t: diffs = twelve 0s, three +5000s, three +4000s -> mean 1500,
# sum sq dev 82.5e6, sample var 4,852,941.18, se 519.2377, t = 2.88885.
# ---------------------------------------------------------------------------

def _shared_year(year: int) -> list:
    return [
        _trade(1000.0, "long", f"{year}-01-05T14:31:00Z", f"{year}-01-05"),
        _trade(-500.0, "long", f"{year}-02-05T14:31:00Z", f"{year}-02-05"),
        _trade(800.0, "short", f"{year}-03-05T14:31:00Z", f"{year}-03-05"),
        _trade(-300.0, "short", f"{year}-04-05T14:31:00Z", f"{year}-04-05"),
    ]


def _extras_year(year: int) -> list:
    return [
        _trade(5000.0, "long", f"{year}-05-05T14:31:00Z", f"{year}-05-05"),
        _trade(4000.0, "short", f"{year}-06-05T14:31:00Z", f"{year}-06-05"),
    ]


def _all_pass_pair() -> tuple[CellResult, CellResult]:
    baseline = tuple(t for y in (2023, 2024, 2025) for t in _shared_year(y))
    cell = tuple(t for y in (2023, 2024, 2025) for t in _shared_year(y) + _extras_year(y))
    return (
        CellResult(overrides={"ma_50_length": 40}, trades=cell),
        CellResult(overrides={}, trades=baseline),
    )


def test_score_cell_all_rows_pass():
    cell, baseline = _all_pass_pair()
    score = score_cell(cell, baseline)
    assert score.trade_count == 18
    assert score.net_pnl == 30000.0
    assert score.delta_vs_baseline == 27000.0
    for name, row in score.rows.items():
        assert row["pass"], f"row {name} unexpectedly failed: {row}"
    assert score.rows["trade_count"]["needs_justification"] is False
    assert score.rows["paired_t"]["t"] == pytest.approx(2.88885, abs=1e-4)
    assert score.rows["paired_t"]["n_sessions"] == 18
    assert score.passes_all is True


# ---------------------------------------------------------------------------
# Outlier-carried fixture. Baseline: 2023 has three +1000 longs plus
# (+800S, -500L, -300S, -800S); 2024/2025 have (+1000L, +800S, -500L,
# -300S, -800S). Baseline net 2600, count 17, max DD -1600.
# Cell: 2023's three +1000 longs degraded to +500 (same sessions), plus a
# single +14000 long on a new session. Cell net 15100, delta 12500.
#
# Expected: passes materiality/expectancy/drawdown/year_by_year/
# side_symmetry, trade_count unflagged, but FAILS outlier_survival
# (15100-14000=1100 < baseline-without-top1 1600) and FAILS paired_t
# (t = 0.8858 -- a single-session gain is not a reliable daily edge).
# The two failures are correlated by design: the paired t and the outlier
# cut are both built to catch exactly this shape. The spec's testing
# section sketched this fixture as failing only row 5; the paired t
# co-failing is mathematically inherent (3 outlier sessions out of 18
# cannot clear t>=2) and row 5's isolated logic is already covered by
# test_net_without_top_removes_largest_winners.
# ---------------------------------------------------------------------------

def _outlier_baseline_year(year: int) -> list:
    if year == 2023:
        return [
            _trade(1000.0, "long", "2023-01-05T14:31:00Z", "2023-01-05"),
            _trade(1000.0, "long", "2023-01-06T14:31:00Z", "2023-01-06"),
            _trade(1000.0, "long", "2023-01-07T14:31:00Z", "2023-01-07"),
            _trade(800.0, "short", "2023-02-05T14:31:00Z", "2023-02-05"),
            _trade(-500.0, "long", "2023-03-05T14:31:00Z", "2023-03-05"),
            _trade(-300.0, "short", "2023-04-05T14:31:00Z", "2023-04-05"),
            _trade(-800.0, "short", "2023-05-05T14:31:00Z", "2023-05-05"),
        ]
    return [
        _trade(1000.0, "long", f"{year}-01-05T14:31:00Z", f"{year}-01-05"),
        _trade(800.0, "short", f"{year}-02-05T14:31:00Z", f"{year}-02-05"),
        _trade(-500.0, "long", f"{year}-03-05T14:31:00Z", f"{year}-03-05"),
        _trade(-300.0, "short", f"{year}-04-05T14:31:00Z", f"{year}-04-05"),
        _trade(-800.0, "short", f"{year}-05-05T14:31:00Z", f"{year}-05-05"),
    ]


def test_score_cell_outlier_carried_gain_fails_outlier_and_t_rows():
    baseline_trades = tuple(
        t for y in (2023, 2024, 2025) for t in _outlier_baseline_year(y)
    )
    cell_trades = []
    for t in _outlier_baseline_year(2023):
        if t.side == "long" and t.net_pnl == 1000.0:
            cell_trades.append(
                _trade(500.0, "long", t.entry_timestamp_utc, t.session_date)
            )
        else:
            cell_trades.append(t)
    cell_trades.append(_trade(14000.0, "long", "2023-07-05T14:31:00Z", "2023-07-05"))
    cell_trades += _outlier_baseline_year(2024) + _outlier_baseline_year(2025)

    score = score_cell(
        CellResult(overrides={"ma_50_length": 30}, trades=tuple(cell_trades)),
        CellResult(overrides={}, trades=baseline_trades),
    )
    assert score.net_pnl == 15100.0
    assert score.delta_vs_baseline == 12500.0
    assert score.rows["materiality"]["pass"] is True
    assert score.rows["expectancy"]["pass"] is True
    assert score.rows["trade_count"]["needs_justification"] is False
    assert score.rows["drawdown"]["pass"] is True
    assert score.rows["year_by_year"]["pass"] is True
    assert score.rows["side_symmetry"]["pass"] is True
    assert score.rows["outlier_survival"]["pass"] is False
    assert score.rows["paired_t"]["pass"] is False
    assert score.rows["paired_t"]["t"] == pytest.approx(0.8858, abs=1e-3)
    assert score.passes_all is False


def test_score_cell_trade_count_drop_is_flag_only():
    # Cell keeps only 2 of baseline's 12 trades (an >20% drop) but with
    # massively improved trades everywhere it does fire -- every scored row
    # can still pass while needs_justification flips on.
    _, baseline = _all_pass_pair()
    cell_trades = tuple(
        _trade(net, side, f"{y}-01-05T14:31:00Z", f"{y}-01-05")
        for y, net, side in (
            (2023, 9000.0, "long"), (2023, 8000.0, "short"),
            (2024, 9000.0, "long"), (2024, 8000.0, "short"),
            (2025, 9000.0, "long"), (2025, 8000.0, "short"),
        )
    )
    score = score_cell(CellResult(overrides={"ma_50_length": 70}, trades=cell_trades), baseline)
    assert score.trade_count == 6
    assert score.rows["trade_count"]["needs_justification"] is True
    assert score.rows["trade_count"]["pass"] is True  # flag-only, never fails


def test_score_cell_empty_cell_fails_without_crashing():
    _, baseline = _all_pass_pair()
    score = score_cell(CellResult(overrides={"ma_50_length": 30}, trades=()), baseline)
    assert score.trade_count == 0
    assert score.net_pnl == 0.0
    assert score.rows["materiality"]["pass"] is False
    assert score.rows["expectancy"]["pass"] is False
    assert score.passes_all is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sweep.py -v`
Expected: the 7 Task-1 tests pass; the 4 new tests FAIL with `ImportError: cannot import name 'score_cell'`

- [ ] **Step 3: Write the implementation**

Append to `src/full_python/research/sweep.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_sweep.py -v`
Expected: 11 passed

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 148 passed, 2 skipped

- [ ] **Step 6: Commit**

```bash
git add src/full_python/research/sweep.py tests/test_sweep.py
git commit -m "feat: score_cell -- train promotion rows with paired session t"
```

---

### Task 3: select_qualifier and run_grid

**Files:**
- Modify: `src/full_python/research/sweep.py` (imports + append)
- Modify: `tests/test_sweep.py` (append)

**Interfaces:**
- Consumes: `CellResult`, `CellScore`, `score_cell` (Task 2). From existing code: `AdaptiveTrendConfig` (frozen dataclass with `.to_dict()` and `.parameter_hash()`), `AdaptiveTrendStrategy(config)`, `SimulationEngine(sim_config).run(bars, strategy)` returning a result with `.trades: tuple[Trade, ...]`, `production_am_config()`.
- Produces:
  - `select_qualifier(scores: Sequence[CellScore]) -> Optional[CellScore]`
  - `run_grid(bars, base_config, overrides_list, sim_config, train_start, train_end) -> list[CellResult]` — `bars: Sequence[MarketBar]`, `base_config: AdaptiveTrendConfig`, `overrides_list: Sequence[dict]`, `sim_config: SimulationConfig`, `train_start`/`train_end`: ISO-8601 UTC strings compared lexicographically against `Trade.entry_timestamp_utc` (inclusive start, exclusive end).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sweep.py`:

```python
from full_python.models import MarketBar
from full_python.research.sweep import run_grid, select_qualifier
from full_python.simulation import SimulationConfig
from full_python.strategy.adaptive_trend_config import production_am_config


def _score(overrides: dict, net: float, passes: bool) -> CellScore:
    return CellScore(
        overrides=overrides, trade_count=100, net_pnl=net,
        delta_vs_baseline=net - 50000.0, rows={}, passes_all=passes,
    )


def test_select_qualifier_none_pass():
    scores = [_score({"ma_50_length": 30}, 60000.0, False),
              _score({"ma_50_length": 40}, 70000.0, False)]
    assert select_qualifier(scores) is None


def test_select_qualifier_best_net_among_passers():
    scores = [
        _score({"ma_50_length": 30}, 80000.0, True),
        _score({"ma_50_length": 40}, 90000.0, True),
        _score({"ma_50_length": 60}, 95000.0, False),  # higher net but fails
    ]
    winner = select_qualifier(scores)
    assert winner is not None
    assert winner.overrides == {"ma_50_length": 40}


def test_select_qualifier_never_returns_baseline():
    scores = [_score({}, 99000.0, True), _score({"ma_50_length": 40}, 61000.0, True)]
    winner = select_qualifier(scores)
    assert winner is not None
    assert winner.overrides == {"ma_50_length": 40}


def _smoke_bars() -> list[MarketBar]:
    # Two RTH sessions of flat 1-min bars inside the train window. Flat
    # prices + MA warmup guarantee zero trades; we only exercise plumbing.
    bars = []
    for day in ("2023-05-01", "2023-05-02"):
        for minute in range(31, 60):  # 14:31Z-14:59Z = 9:31-9:59 ET (EDT)
            bars.append(MarketBar(
                timestamp_utc=f"{day}T14:{minute:02d}:00Z", symbol="NQ",
                open=100.0, high=100.0, low=100.0, close=100.0, volume=10.0,
            ))
    return bars


def test_run_grid_smoke_baseline_identity_and_override_divergence():
    results = run_grid(
        _smoke_bars(), production_am_config(),
        [{}, {"ma_50_length": 40}], SimulationConfig(),
        "2023-01-01T00:00:00Z", "2025-07-01T00:00:00Z",
    )
    assert len(results) == 2
    assert all(r.error is None for r in results)
    assert all(r.trades == () for r in results)
    assert results[0].config_hash == production_am_config().parameter_hash()
    assert results[1].config_hash is not None
    assert results[1].config_hash != results[0].config_hash


def test_run_grid_captures_cell_error_and_continues():
    results = run_grid(
        _smoke_bars(), production_am_config(),
        [{"nonexistent_field": 1}, {}], SimulationConfig(),
        "2023-01-01T00:00:00Z", "2025-07-01T00:00:00Z",
    )
    assert len(results) == 2
    assert results[0].error is not None
    assert "nonexistent_field" in results[0].error
    assert results[0].trades == ()
    assert results[1].error is None  # the grid continued past the failure
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sweep.py -v`
Expected: 11 pass; 5 new FAIL with `ImportError: cannot import name 'run_grid'`

- [ ] **Step 3: Write the implementation**

In `src/full_python/research/sweep.py`, extend the imports at the top of the file:

```python
from full_python.models import MarketBar, Trade
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import AdaptiveTrendConfig
```

(replacing the existing `from full_python.models import Trade` line), then append:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_sweep.py -v`
Expected: 16 passed

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 153 passed, 2 skipped

- [ ] **Step 6: Commit**

```bash
git add src/full_python/research/sweep.py tests/test_sweep.py
git commit -m "feat: run_grid and the pre-registered select_qualifier rule"
```

---

### Task 4: the MA-length sweep driver

**Files:**
- Create: `scripts/sweep_ma_lengths.py`
- Create: `tests/test_sweep_driver.py`

**Interfaces:**
- Consumes: `run_grid`, `score_cell`, `select_qualifier` (Tasks 2-3, exact signatures above); `full_python.cli.TRADE_CSV_COLUMNS` (list of trade CSV field names, matches `Trade.to_payload()` keys); `full_python.data.loaders.CsvBarColumnMap` / `load_csv_bars`; `scripts.freeze_baseline_anchor.FROZEN_SIMULATION_OVERRIDES` (dict of SimulationConfig kwargs — the frozen cost model); `production_am_config()`.
- Produces: `build_grid() -> list[dict]` and module constants `GRID_MA_50`, `GRID_MA_200`, `BASELINE_CELL` (imported by the test to pin the pre-registered grid).

- [ ] **Step 1: Write the failing test**

Create `tests/test_sweep_driver.py`:

```python
from scripts.sweep_ma_lengths import BASELINE_CELL, GRID_MA_50, GRID_MA_200, build_grid


def test_grid_is_preregistered_5x5():
    # These literals are locked by the design spec
    # (docs/superpowers/specs/2026-07-05-sweep-harness-design.md).
    # Changing them is changing the registered experiment -- this test
    # exists to make that impossible to do silently.
    assert GRID_MA_50 == (30, 40, 50, 60, 70)
    assert GRID_MA_200 == (100, 150, 200, 250, 300)
    assert BASELINE_CELL == (50, 200)

    grid = build_grid()
    assert len(grid) == 25
    assert grid.count({}) == 1  # baseline is the empty-override cell
    pairs = {
        (c.get("ma_50_length", 50), c.get("ma_200_length", 200)) for c in grid
    }
    assert len(pairs) == 25
    assert BASELINE_CELL in pairs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sweep_driver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.sweep_ma_lengths'`

- [ ] **Step 3: Write the driver**

Create `scripts/sweep_ma_lengths.py`:

```python
#!/usr/bin/env python3
"""Pre-registered Gate 1 Phase 4 sweep: ma_50_length x ma_200_length.

Grid locked by docs/superpowers/specs/2026-07-05-sweep-harness-design.md
and pinned by tests/test_sweep_driver.py. Runs the train window only;
NEVER touches holdout. Row 8 (slippage sensitivity) is run separately
for the selected qualifier only, before any holdout decision.

Usage: PYTHONPATH=src:. python3 scripts/sweep_ma_lengths.py
Expected runtime: ~17 minutes (25 cells x ~41s).
"""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from full_python.cli import TRADE_CSV_COLUMNS
from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.research.sweep import run_grid, score_cell, select_qualifier
from full_python.simulation import SimulationConfig
from full_python.strategy.adaptive_trend_config import production_am_config
from scripts.freeze_baseline_anchor import FROZEN_SIMULATION_OVERRIDES

BARS_CSV = Path("runs/multi-year/nq1_2021-03-16_2026-06-26.csv")
OUT_DIR = Path("runs/sweeps/ma-grid")
# Truncated bar window validated 2026-07-05 to reproduce the full-history
# train baseline exactly (n=378, net=$65,855).
BARS_START = "2022-11-01T00:00:00Z"
BARS_END = "2025-07-01T00:00:00Z"
TRAIN_START = "2023-01-01T00:00:00Z"
TRAIN_END = "2025-07-01T00:00:00Z"
GRID_MA_50 = (30, 40, 50, 60, 70)
GRID_MA_200 = (100, 150, 200, 250, 300)
BASELINE_CELL = (50, 200)

SCORE_CSV_COLUMNS = [
    "ma_50", "ma_200", "error", "trade_count", "net_pnl", "delta",
    "materiality_pass", "expectancy_pass", "count_flag", "drawdown_pass",
    "outlier_pass", "years_pass", "sides_pass", "t", "t_pass", "passes_all",
]


def build_grid() -> list[dict]:
    cells = []
    for ma_50 in GRID_MA_50:
        for ma_200 in GRID_MA_200:
            if (ma_50, ma_200) == BASELINE_CELL:
                cells.append({})
            else:
                cells.append({"ma_50_length": ma_50, "ma_200_length": ma_200})
    return cells


def _cell_pair(overrides: dict) -> tuple[int, int]:
    return (
        overrides.get("ma_50_length", BASELINE_CELL[0]),
        overrides.get("ma_200_length", BASELINE_CELL[1]),
    )


def main() -> int:
    if not BARS_CSV.exists():
        print(f"ERROR: bars file not found: {BARS_CSV}", file=sys.stderr)
        return 1
    column_map = CsvBarColumnMap(
        timestamp="timestamp", symbol="symbol", open="open",
        high="high", low="low", close="close", volume="volume",
    )
    print(f"loading bars from {BARS_CSV} ...", flush=True)
    bars = [
        b for b in load_csv_bars(str(BARS_CSV), column_map)
        if BARS_START <= b.timestamp_utc < BARS_END
    ]
    print(f"{len(bars)} bars in [{BARS_START}, {BARS_END})", flush=True)

    sim_config = SimulationConfig(**FROZEN_SIMULATION_OVERRIDES)
    grid = build_grid()
    print(f"running {len(grid)} cells ...", flush=True)
    results = run_grid(
        bars, production_am_config(), grid, sim_config, TRAIN_START, TRAIN_END
    )

    baseline = next(r for r in results if r.overrides == {})
    if baseline.error is not None:
        print(f"ERROR: baseline cell failed: {baseline.error}", file=sys.stderr)
        return 1

    cells_dir = OUT_DIR / "cells"
    cells_dir.mkdir(parents=True, exist_ok=True)
    scores = {}
    for result in results:
        ma_50, ma_200 = _cell_pair(result.overrides)
        with (cells_dir / f"ma50_{ma_50}_ma200_{ma_200}.trades.csv").open(
            "w", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=TRADE_CSV_COLUMNS)
            writer.writeheader()
            for trade in result.trades:
                writer.writerow(trade.to_payload())
        if result.error is None:
            scores[(ma_50, ma_200)] = score_cell(result, baseline)

    qualifier = select_qualifier(list(scores.values()))

    with (OUT_DIR / "scores.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCORE_CSV_COLUMNS)
        writer.writeheader()
        for result in results:
            ma_50, ma_200 = _cell_pair(result.overrides)
            if result.error is not None:
                writer.writerow({"ma_50": ma_50, "ma_200": ma_200, "error": result.error})
                continue
            score = scores[(ma_50, ma_200)]
            rows = score.rows
            writer.writerow({
                "ma_50": ma_50, "ma_200": ma_200, "error": "",
                "trade_count": score.trade_count,
                "net_pnl": score.net_pnl,
                "delta": score.delta_vs_baseline,
                "materiality_pass": rows["materiality"]["pass"],
                "expectancy_pass": rows["expectancy"]["pass"],
                "count_flag": rows["trade_count"]["needs_justification"],
                "drawdown_pass": rows["drawdown"]["pass"],
                "outlier_pass": rows["outlier_survival"]["pass"],
                "years_pass": rows["year_by_year"]["pass"],
                "sides_pass": rows["side_symmetry"]["pass"],
                "t": rows["paired_t"]["t"],
                "t_pass": rows["paired_t"]["pass"],
                "passes_all": score.passes_all,
            })

    summary = {
        "registered_grid": {
            "ma_50_length": list(GRID_MA_50),
            "ma_200_length": list(GRID_MA_200),
            "baseline_cell": list(BASELINE_CELL),
        },
        "bars_window": [BARS_START, BARS_END],
        "train_window": [TRAIN_START, TRAIN_END],
        "sim_config": dict(FROZEN_SIMULATION_OVERRIDES),
        "base_config_hash": production_am_config().parameter_hash(),
        "baseline": {
            "trade_count": len(baseline.trades),
            "net_pnl": sum(t.net_pnl for t in baseline.trades),
        },
        "cells": [asdict(score) for score in scores.values()],
        "errors": {
            f"ma50_{_cell_pair(r.overrides)[0]}_ma200_{_cell_pair(r.overrides)[1]}": r.error
            for r in results if r.error is not None
        },
        "qualifier": qualifier.overrides if qualifier is not None else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with (OUT_DIR / "summary.json").open("w") as handle:
        json.dump(summary, handle, indent=2)

    print()
    print(f"{'ma50':>5} {'ma200':>6} {'n':>5} {'net':>10} {'delta':>10} "
          f"{'t':>7}  rows(mat/exp/dd/out/yr/side/t)  ALL")
    for (ma_50, ma_200), score in sorted(
        scores.items(), key=lambda kv: kv[1].net_pnl, reverse=True
    ):
        rows = score.rows
        flags = "".join(
            "P" if rows[k]["pass"] else "-"
            for k in ("materiality", "expectancy", "drawdown",
                      "outlier_survival", "year_by_year", "side_symmetry",
                      "paired_t")
        )
        t_stat = rows["paired_t"]["t"]
        t_text = f"{t_stat:7.2f}" if t_stat is not None else "   None"
        marker = " BASELINE" if (ma_50, ma_200) == BASELINE_CELL else ""
        print(f"{ma_50:>5} {ma_200:>6} {score.trade_count:>5} "
              f"{score.net_pnl:>10.0f} {score.delta_vs_baseline:>+10.0f} "
              f"{t_text}  {flags:^31}  {'YES' if score.passes_all else 'no'}"
              f"{marker}")
    print()
    if qualifier is None:
        print("NO QUALIFIER -- no cell passed every scored row. Per the "
              "pre-registered rule the MA axis pair closes on train "
              "evidence (pending the written decision doc).")
    else:
        print(f"QUALIFIER: {qualifier.overrides} "
              f"(net ${qualifier.net_pnl:,.0f}, "
              f"delta {qualifier.delta_vs_baseline:+,.0f}). Next steps: "
              "row 8 slippage runs for this cell only, then the one-shot "
              "holdout -- both deliberate follow-ups, not automatic.")
    print(f"outputs written to {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_sweep_driver.py -v`
Expected: 1 passed

- [ ] **Step 5: Verify the missing-bars error path**

The worktree has no `runs/multi-year/` data (gitignored), so the driver must exit nonzero with a clear message:

Run: `PYTHONPATH=src:. python3 scripts/sweep_ma_lengths.py; echo "exit=$?"`
Expected output: `ERROR: bars file not found: runs/multi-year/nq1_2021-03-16_2026-06-26.csv` and `exit=1`

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 154 passed, 2 skipped

- [ ] **Step 7: Commit**

```bash
git add scripts/sweep_ma_lengths.py tests/test_sweep_driver.py
git commit -m "feat: pre-registered ma_50 x ma_200 sweep driver"
```

---

## Not in this plan (deliberate)

- **Running the actual 25-cell sweep** — happens after merge, from the main clone where the bars CSV exists, as its own reviewed step.
- Row 8 slippage runs, the evaluation/closing decision doc, and the holdout step.
- The `sr_min_stop_distance` × `sr_stop_buffer` driver (next sweep job; reuses this library with its own pre-registered grid).
- Parallelism (sequential ~17 min is acceptable).
