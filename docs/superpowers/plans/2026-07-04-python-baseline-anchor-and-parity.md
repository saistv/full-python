# Python Baseline Anchor & Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze a canonical, code-versioned Python baseline (replacing the missing TV export as the reference point), prove it against TradingView with a decomposed parity report, build the metrics/report tooling and golden-trade regression suite that protect it, extract a shared risk layer behind that safety net, and run a data-honest sizing gate — Steps 1–7 of the larger Python Reference Engine Migration, resequenced by dependency (see Global Constraints).

**Architecture:** All work lands on `claude/m4-regime` (the actual migration tip — confirmed via `git ls-tree` to hold `events.py`, `simulation/engine.py`, `strategy/adaptive_trend.py`, `strategy/vwap_reversion.py`, `regime.py`, and the fill-simulation-policy decision doc; 101 tests passing). Every task is additive or a behavior-preserving refactor proven by tests — nothing here changes strategy logic. New code lives in `src/full_python/reporting/metrics.py` (new), `src/full_python/parity_report.py` (new), `src/full_python/risk/` (new package, extracted from `simulation/engine.py`), and `src/full_python/cli.py` (modified for the code-hash `run_id` component).

**Tech Stack:** Python 3, `pytest`, stdlib only (`dataclasses`, `hashlib`, `json`, `statistics`, `subprocess`, `csv`). No new dependencies.

## Global Constraints

- Branch: all tasks commit to `claude/m4-regime` (already checked out at `/Users/sais/Documents/New Beginning/full-python`, tracking `origin/claude/m4-regime`). Do not touch `codex/real-data-baseline-report` or `main` — they are unrelated/stale branches.
- **Data-span decision (locked 2026-07-04):** the baseline anchor freezes on the 9-month window that is actually reconciled — 2025-10-01 → 2026-06-26 (260,681 bars), **not** the spec's aspirational 3-year window (no 3-year NQ continuous dataset exists in this repo). This is a deliberate, explicit scope-down, not an oversight — it must be stated verbatim in the anchor decision doc (Task 4) and in the sizing-gate doc (Task 8), because n=120 trades cannot support Gate 1's full train/holdout + top-1/2/3-removal robustness protocol. Do not silently upgrade the claim.
- **Reordering vs. the original spec numbering:** the spec lists "freeze the anchor" as step 1 and "add code-hash to run_id" as step 6. The anchor's own definition requires an "exact code hash... new, since run_id today only hashes data+config, not code" — so the code-hash must exist *before* the freeze is meaningful. This plan builds Tasks 1–3 (metrics tooling + code-hash) before Task 4 (the freeze itself). Do not reorder back to the spec's literal numbering.
- Cost model for every baseline/parity run in this plan (matches the existing TV reconciliation in `docs/decisions/2026-07-03-first-tv-reconciliation.md`, do not substitute `SimulationConfig`'s defaults): `--point-value 20 --commission-rt 10 --entry-slippage-points 0.75 --exit-slippage-points 0.75`.
- Strategy for the freeze: `adaptive_trend_am` (`production_am_config()`) — the production AM+DLL stack, already reconciled 106/106 against TV per `docs/decisions/2026-07-03-m2b-am-dll-reconciliation.md`. This is a deliberate choice over the flat `adaptive_trend` core because AM+DLL is what any promotion path actually deploys; state this explicitly in Task 4's decision doc.
- The local 260,681-bar CSV for 2025-10-01→2026-06-26 is **not checked into git** (`runs/*` is gitignored). Tasks 4, 5, and 6 read it from a path supplied via the environment variable `FULL_PYTHON_BASELINE_DATA`; tests that need it skip (not fail) when the variable is unset, via `pytest.mark.skipif`. If the file is missing, regenerate it with `python -m full_python.data.databento` (see `src/full_python/data/databento.py:228` `main()`) against the same Databento GLBX raw files used for the original reconciliation.
- `python3 -m pytest -q` must stay green (101 tests today) after every task; each task adds tests, never removes coverage.
- Every new dataclass follows the existing codebase convention: `@dataclass(frozen=True)` with a `to_dict()` method using `asdict()`.
- Commit after each task following existing commit style (`feat: ...` / `docs: ...`), one commit per task.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/full_python/reporting/metrics.py` (new) | Trade-level expectancy, R-multiple, win/loss-streak, and exit-reason-breakdown calculations. Pure functions/dataclasses over `list[Trade]`; no I/O. |
| `tests/test_metrics.py` (new) | Unit tests for every function in `metrics.py`. |
| `src/full_python/cli.py` (modified) | Adds `_code_version_hash()` and folds it into `run_id` and `report.json`. |
| `tests/test_cli_trades.py` (modified) | Adds assertions for the new `run_id` shape and `code_version` field. |
| `docs/decisions/2026-07-04-python-baseline-anchor.md` (new) | The frozen anchor: exact config/data/code/cost-model hashes, canonical metrics, explicit data-span caveat. |
| `runs/baseline-anchor/` (new, gitignored) | The canonical `report.json`, `trades.csv`, `events.jsonl` for the frozen run. |
| `scripts/freeze_baseline_anchor.py` (new) | One script that reproduces the anchor run deterministically from `FULL_PYTHON_BASELINE_DATA`. |
| `src/full_python/parity_report.py` (new) | Decomposed parity table (entry/exit timestamp, price, reason, trade count, largest-20 deltas) built on top of `reconcile.py`'s existing matching. |
| `tests/test_parity_report.py` (new) | Unit tests for the decomposition logic. |
| `docs/decisions/2026-07-04-parity-delta-report.md` (new) | The rendered decomposition table for the frozen anchor run + manual review of the largest 20 deltas. |
| `tests/test_golden_trades.py` (new) | Golden-trade regression suite: replays the frozen window through `SimulationEngine` and asserts exact reproduction against a serialized fixture. |
| `tests/fixtures/golden_trades.json` (new) | Serialized expected trades for the golden-trade suite. |
| `src/full_python/risk/__init__.py`, `session_rules.py`, `position_limits.py`, `daily_loss.py`, `risk_manager.py` (new package) | Risk-gate logic extracted from `simulation/engine.py`, unchanged behavior. |
| `src/full_python/simulation/engine.py` (modified) | Delegates `_veto_reason` and `_check_daily_loss_limit` to the new `risk/` package instead of inline logic. |
| `tests/test_risk_manager.py` (new) | Unit tests for the extracted risk modules in isolation (previously only reachable through `SimulationEngine`). |
| `docs/decisions/2026-07-04-sizing-research-gate.md` (new) | Capital-allocation candidates run against the 9-month window, with the data-limitation caveat stated up front. |

---

### Task 1: `metrics.py` — expectancy, R-multiple, streaks, exit-reason breakdown

**Files:**
- Create: `src/full_python/reporting/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `full_python.models.Trade` (existing fields: `entry_price, exit_price, stop_price, quantity, net_pnl, gross_pnl, exit_reason, ...` — see `src/full_python/models.py:207`). No new field is added to `Trade`; initial risk is derived from `entry_price`/`stop_price` since Adaptive Trend's stop is frozen at entry (confirmed: `strategy/adaptive_trend.py` never revises `stop_price` after entry).
- Produces (used by Task 2 and Task 8): `initial_risk_points(trade) -> float`, `r_multiple(trade, point_value) -> float | None`, `ExpectancyReport` (dataclass), `build_expectancy_report(trades, *, point_value) -> ExpectancyReport`, `ExitReasonBucket` (dataclass), `build_exit_reason_breakdown(trades, *, point_value) -> list[ExitReasonBucket]`, `max_win_streak(trades) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_metrics.py
from full_python.models import Trade
from full_python.reporting.metrics import (
    build_exit_reason_breakdown,
    build_expectancy_report,
    initial_risk_points,
    max_win_streak,
    r_multiple,
)


def _trade(
    *,
    side: str = "long",
    entry_price: float = 100.0,
    exit_price: float = 105.0,
    stop_price: float = 95.0,
    exit_reason: str = "atf_flip",
    net_pnl: float = 100.0,
    gross_pnl: float = 100.0,
    quantity: int = 1,
) -> Trade:
    return Trade(
        symbol="NQU2026",
        side=side,
        quantity=quantity,
        entry_timestamp_utc="2026-01-05T14:30:00Z",
        entry_price=entry_price,
        exit_timestamp_utc="2026-01-05T14:45:00Z",
        exit_price=exit_price,
        exit_reason=exit_reason,
        stop_price=stop_price,
        gross_points=exit_price - entry_price if side == "long" else entry_price - exit_price,
        gross_pnl=gross_pnl,
        commission=1.0,
        net_pnl=net_pnl,
        mfe_points=5.0,
        mae_points=1.0,
        session_date="2026-01-05",
    )


def test_initial_risk_points_is_distance_from_entry_to_frozen_stop() -> None:
    trade = _trade(entry_price=100.0, stop_price=95.0)
    assert initial_risk_points(trade) == 5.0


def test_initial_risk_points_handles_short_side_symmetrically() -> None:
    trade = _trade(side="short", entry_price=100.0, stop_price=105.0)
    assert initial_risk_points(trade) == 5.0


def test_r_multiple_expresses_net_pnl_in_units_of_initial_dollar_risk() -> None:
    # risk = 5 points * $20/point * 1 contract = $100; net_pnl=$250 -> R=2.5
    trade = _trade(entry_price=100.0, stop_price=95.0, net_pnl=250.0)
    assert r_multiple(trade, point_value=20.0) == 2.5


def test_r_multiple_is_none_when_stop_equals_entry() -> None:
    trade = _trade(entry_price=100.0, stop_price=100.0)
    assert r_multiple(trade, point_value=20.0) is None


def test_expectancy_report_computes_win_rate_and_average_win_loss() -> None:
    trades = [
        _trade(net_pnl=200.0, entry_price=100.0, stop_price=95.0),
        _trade(net_pnl=-100.0, entry_price=100.0, stop_price=95.0),
        _trade(net_pnl=0.0, entry_price=100.0, stop_price=95.0),
    ]

    report = build_expectancy_report(trades, point_value=20.0)

    assert report.trade_count == 3
    assert report.win_count == 1
    assert report.loss_count == 1
    assert report.scratch_count == 1
    assert report.win_rate == 1 / 3
    assert report.avg_win_dollars == 200.0
    assert report.avg_loss_dollars == 100.0
    assert report.expectancy_dollars == (200.0 - 100.0 + 0.0) / 3
    # R for each: 200/100=2.0, -100/100=-1.0, 0/100=0.0
    assert report.avg_r_multiple == (2.0 - 1.0 + 0.0) / 3
    assert report.r_multiples_computed == 3


def test_expectancy_report_on_empty_trades_is_all_zero_not_a_crash() -> None:
    report = build_expectancy_report([], point_value=20.0)

    assert report.trade_count == 0
    assert report.win_rate == 0.0
    assert report.avg_r_multiple is None


def test_exit_reason_breakdown_groups_and_sorts_by_reason() -> None:
    trades = [
        _trade(exit_reason="stop", net_pnl=-100.0, entry_price=100.0, stop_price=95.0),
        _trade(exit_reason="atf_flip", net_pnl=200.0, entry_price=100.0, stop_price=95.0),
        _trade(exit_reason="atf_flip", net_pnl=50.0, entry_price=100.0, stop_price=95.0),
    ]

    buckets = build_exit_reason_breakdown(trades, point_value=20.0)

    assert [b.exit_reason for b in buckets] == ["atf_flip", "stop"]
    atf_bucket = buckets[0]
    assert atf_bucket.trade_count == 2
    assert atf_bucket.net_pnl == 250.0
    assert atf_bucket.win_rate == 1.0


def test_max_win_streak_counts_consecutive_wins_and_resets_on_non_win() -> None:
    trades = [
        _trade(net_pnl=10.0),
        _trade(net_pnl=10.0),
        _trade(net_pnl=-5.0),
        _trade(net_pnl=10.0),
        _trade(net_pnl=10.0),
        _trade(net_pnl=10.0),
    ]

    assert max_win_streak(trades) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_metrics.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'full_python.reporting.metrics'`

- [ ] **Step 3: Write the implementation**

```python
# src/full_python/reporting/metrics.py
"""Trade-level expectancy, R-multiple, streak, and exit-reason metrics.

Complements reporting/survivability.py (drawdown, loss-streak, top-trade
dependency) with the risk-normalized metrics Gate 1 needs to compare
candidates that shift average win/loss size, not just net P&L. No new
field is added to Trade: Adaptive Trend's stop is frozen at entry and
never revised (strategy/adaptive_trend.py), so Trade.stop_price already
IS the initial stop, and initial risk is derived, not stored.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import statistics
from typing import Any, Iterable, Optional

from full_python.models import Trade


def initial_risk_points(trade: Trade) -> float:
    return abs(trade.entry_price - trade.stop_price)


def r_multiple(trade: Trade, point_value: float) -> Optional[float]:
    risk_dollars = initial_risk_points(trade) * point_value * trade.quantity
    if risk_dollars <= 0:
        return None
    return trade.net_pnl / risk_dollars


@dataclass(frozen=True)
class ExpectancyReport:
    trade_count: int
    win_count: int
    loss_count: int
    scratch_count: int
    win_rate: float
    avg_win_dollars: float
    avg_loss_dollars: float
    expectancy_dollars: float
    avg_r_multiple: Optional[float]
    median_r_multiple: Optional[float]
    r_multiples_computed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_expectancy_report(trades: Iterable[Trade], *, point_value: float) -> ExpectancyReport:
    trades = list(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    scratches = [t for t in trades if t.net_pnl == 0]
    r_values = [r for r in (r_multiple(t, point_value) for t in trades) if r is not None]

    return ExpectancyReport(
        trade_count=len(trades),
        win_count=len(wins),
        loss_count=len(losses),
        scratch_count=len(scratches),
        win_rate=(len(wins) / len(trades)) if trades else 0.0,
        avg_win_dollars=(sum(t.net_pnl for t in wins) / len(wins)) if wins else 0.0,
        avg_loss_dollars=(abs(sum(t.net_pnl for t in losses)) / len(losses)) if losses else 0.0,
        expectancy_dollars=(sum(t.net_pnl for t in trades) / len(trades)) if trades else 0.0,
        avg_r_multiple=(sum(r_values) / len(r_values)) if r_values else None,
        median_r_multiple=(statistics.median(r_values)) if r_values else None,
        r_multiples_computed=len(r_values),
    )


@dataclass(frozen=True)
class ExitReasonBucket:
    exit_reason: str
    trade_count: int
    net_pnl: float
    win_rate: float
    avg_r_multiple: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_exit_reason_breakdown(
    trades: Iterable[Trade], *, point_value: float
) -> list[ExitReasonBucket]:
    buckets: dict[str, list[Trade]] = {}
    for trade in trades:
        buckets.setdefault(trade.exit_reason, []).append(trade)

    result = []
    for reason, group in buckets.items():
        wins = [t for t in group if t.net_pnl > 0]
        r_values = [r for r in (r_multiple(t, point_value) for t in group) if r is not None]
        result.append(
            ExitReasonBucket(
                exit_reason=reason,
                trade_count=len(group),
                net_pnl=sum(t.net_pnl for t in group),
                win_rate=(len(wins) / len(group)) if group else 0.0,
                avg_r_multiple=(sum(r_values) / len(r_values)) if r_values else None,
            )
        )
    result.sort(key=lambda bucket: bucket.exit_reason)
    return result


def max_win_streak(trades: Iterable[Trade]) -> int:
    current = 0
    best = 0
    for trade in trades:
        if trade.net_pnl > 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_metrics.py -v`
Expected: 8 passed

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: 109 passed (101 existing + 8 new)

- [ ] **Step 6: Commit**

```bash
git add src/full_python/reporting/metrics.py tests/test_metrics.py
git commit -m "feat: add trade expectancy, R-multiple, and exit-reason metrics"
```

---

### Task 2: Baseline metrics report generator

**Files:**
- Modify: `src/full_python/reporting/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `build_expectancy_report`, `build_exit_reason_breakdown`, `max_win_streak` (Task 1); `full_python.reporting.survivability.build_survivability_report`, `TradeResult` (existing, `src/full_python/reporting/survivability.py:11,31`).
- Produces (used by Task 4's freeze script): `MetricsReport` (dataclass with `.to_dict()`), `build_metrics_report(trades, *, point_value) -> MetricsReport`.

This generator is intentionally scoped to **backtest-level reporting** (what Task 4 needs to freeze the anchor) — not the Continuous Research Layer's live daily/weekly/monthly cadence reports from the wider spec. Those require a running paper/live system (Gate 5+, months away) and are out of scope here; building them now against no live data would be speculative. State this scoping decision in the anchor doc (Task 4), don't silently drop it.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_metrics.py
from full_python.reporting.metrics import MetricsReport, build_metrics_report  # noqa: E402


def test_build_metrics_report_assembles_expectancy_streak_and_exit_breakdown() -> None:
    trades = [
        _trade(net_pnl=200.0, exit_reason="atf_flip", entry_price=100.0, stop_price=95.0),
        _trade(net_pnl=200.0, exit_reason="atf_flip", entry_price=100.0, stop_price=95.0),
        _trade(net_pnl=-100.0, exit_reason="stop", entry_price=100.0, stop_price=95.0),
    ]

    report = build_metrics_report(trades, point_value=20.0)

    assert isinstance(report, MetricsReport)
    assert report.expectancy.trade_count == 3
    assert report.max_win_streak == 2
    assert report.max_loss_streak == 1
    assert [b.exit_reason for b in report.by_exit_reason] == ["atf_flip", "stop"]
    assert report.to_dict()["expectancy"]["trade_count"] == 3
    assert report.to_dict()["by_exit_reason"][0]["exit_reason"] == "atf_flip"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_metrics.py::test_build_metrics_report_assembles_expectancy_streak_and_exit_breakdown -v`
Expected: FAIL with `ImportError: cannot import name 'MetricsReport'`

- [ ] **Step 3: Add the implementation**

```python
# append to src/full_python/reporting/metrics.py

def max_loss_streak(trades: Iterable[Trade]) -> int:
    current = 0
    best = 0
    for trade in trades:
        if trade.net_pnl < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


@dataclass(frozen=True)
class MetricsReport:
    expectancy: ExpectancyReport
    by_exit_reason: list[ExitReasonBucket]
    max_win_streak: int
    max_loss_streak: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "expectancy": self.expectancy.to_dict(),
            "by_exit_reason": [bucket.to_dict() for bucket in self.by_exit_reason],
            "max_win_streak": self.max_win_streak,
            "max_loss_streak": self.max_loss_streak,
        }


def build_metrics_report(trades: Iterable[Trade], *, point_value: float) -> MetricsReport:
    trades = list(trades)
    return MetricsReport(
        expectancy=build_expectancy_report(trades, point_value=point_value),
        by_exit_reason=build_exit_reason_breakdown(trades, point_value=point_value),
        max_win_streak=max_win_streak(trades),
        max_loss_streak=max_loss_streak(trades),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_metrics.py -v`
Expected: 9 passed

- [ ] **Step 5: Run full suite**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: 110 passed

- [ ] **Step 6: Commit**

```bash
git add src/full_python/reporting/metrics.py tests/test_metrics.py
git commit -m "feat: add build_metrics_report generator for baseline freezes"
```

---

### Task 3: Code-hash component in `run_id`

**Files:**
- Modify: `src/full_python/cli.py`
- Test: `tests/test_cli_trades.py`

**Interfaces:**
- Produces: `_code_version_hash() -> str` (module-private in `cli.py`); `run_id` gains a 4th dash-joined segment; `report.json` gains a top-level `"code_version"` key.
- Consumed by: Task 4's freeze script (records `code_version` in the anchor decision doc).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_cli_trades.py
import json
from pathlib import Path

from full_python.cli import run_baseline


def test_run_id_includes_a_code_version_component(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:30:00Z,NQU2026,100,101,99,100,10\n"
        "2026-06-30T13:31:00Z,NQU2026,100,102,99,101,10\n"
        "2026-06-30T13:32:00Z,NQU2026,101,103,100,102.5,10\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "run"

    report_path = run_baseline(data_path=data_path, output_dir=output_dir)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    run_id_parts = report["run_id"].split("-")
    assert len(run_id_parts) == 4
    assert all(len(part) == 8 for part in run_id_parts)
    assert "code_version" in report
    assert len(report["code_version"]) in (40, len("unknown"))
```

(Check the actual existing filename/content of `tests/test_cli_trades.py` first — if `run_baseline`'s import or a `data_path`/`output_dir` helper already exists there under different names, match the file's existing conventions instead of introducing a duplicate import block.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_cli_trades.py::test_run_id_includes_a_code_version_component -v`
Expected: FAIL — `run_id_parts` has length 3, not 4

- [ ] **Step 3: Implement**

```python
# in src/full_python/cli.py, add near the top-level imports:
import subprocess

# add this function near build_strategy():
def _code_version_hash() -> str:
    """Git SHA of the current checkout; 'unknown' outside a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
```

Then in `run_baseline`, replace the existing `run_id` block:

```python
    # Deterministic run identity: same data + same configs => same run id.
    run_id = "-".join(
        [
            manifest.stable_hash()[:8],
            strategy_config.parameter_hash()[:8],
            simulation_config.parameter_hash()[:8],
        ]
    )
```

with:

```python
    # Deterministic run identity: same data + same configs + same code => same run id.
    code_version = _code_version_hash()
    run_id = "-".join(
        [
            manifest.stable_hash()[:8],
            strategy_config.parameter_hash()[:8],
            simulation_config.parameter_hash()[:8],
            code_version[:8],
        ]
    )
```

And in the `report` dict literal, add the field (insert right after the `"run_id": run_id,` line):

```python
    report = {
        "run_id": run_id,
        "code_version": code_version,
        "data": {
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_cli_trades.py tests/test_cli_baseline.py -v`
Expected: all pass (existing `test_cli_baseline.py` assertions on `report["run_id"]`, `report["strategy"]["name"]`, etc. still hold — they don't assert an exact segment count, only presence of specific top-level keys)

- [ ] **Step 5: Run full suite**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: 111 passed

- [ ] **Step 6: Commit**

```bash
git add src/full_python/cli.py tests/test_cli_trades.py
git commit -m "feat: add git-SHA code-version component to run_id"
```

---

### Task 4: Freeze the Python Baseline Anchor

**Files:**
- Create: `scripts/freeze_baseline_anchor.py`
- Create: `docs/decisions/2026-07-04-python-baseline-anchor.md`
- Test: `tests/test_freeze_baseline_anchor.py`

**Interfaces:**
- Consumes: `full_python.cli.run_baseline` (Task 3's updated version), `full_python.reporting.metrics.build_metrics_report` (Task 2).
- Produces: `runs/baseline-anchor/{report.json,trades.csv,events.jsonl,daily_pnl.csv,report.html}` (gitignored, reproducible), plus the committed decision doc recording the hashes and canonical numbers by hand-copying them out of the produced `report.json`.

This is a script, not a new library module — its only job is to call `run_baseline` with the exact frozen inputs and print the hashes needed for the decision doc, so freezing is a one-command, reproducible action rather than a remembered sequence of flags.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_freeze_baseline_anchor.py
import json
from pathlib import Path

import pytest

from scripts.freeze_baseline_anchor import freeze_baseline_anchor


def test_freeze_baseline_anchor_writes_report_with_expected_config(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    # 3 bars is enough to prove the wiring; the real freeze uses FULL_PYTHON_BASELINE_DATA.
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-01-05T14:30:00Z,NQU2026,100,101,99,100,10\n"
        "2026-01-05T14:31:00Z,NQU2026,100,102,99,101,10\n"
        "2026-01-05T14:32:00Z,NQU2026,101,103,100,102.5,10\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "baseline-anchor"

    report_path = freeze_baseline_anchor(data_path=data_path, output_dir=output_dir)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["strategy"]["name"] == "adaptive_trend_v66_flat"
    assert report["strategy"]["enable_anti_martingale"] is True
    assert report["strategy"]["enable_daily_loss_limit"] is True
    assert report["simulation"]["point_value"] == 20.0
    assert report["simulation"]["commission_per_contract_round_trip"] == 10.0
    assert report["simulation"]["entry_slippage_points"] == 0.75
    assert report["simulation"]["exit_slippage_points"] == 0.75
    assert "code_version" in report
    assert "metrics" in report


def test_freeze_baseline_anchor_requires_env_var_when_no_path_given(monkeypatch) -> None:
    monkeypatch.delenv("FULL_PYTHON_BASELINE_DATA", raising=False)
    with pytest.raises(ValueError, match="FULL_PYTHON_BASELINE_DATA"):
        freeze_baseline_anchor(data_path=None, output_dir=Path("/tmp/unused"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_freeze_baseline_anchor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.freeze_baseline_anchor'`

- [ ] **Step 3: Implement**

```python
# scripts/freeze_baseline_anchor.py
"""Freeze the Python Baseline Anchor: one reproducible command.

Replaces the missing 3-year TV export as the reference point (see
docs/decisions/2026-07-04-python-baseline-anchor.md). Data span is the
2025-10-01 -> 2026-06-26 window that is actually reconciled against
TradingView (120/120 trades matched) -- NOT a 3-year window, which does
not exist in this repo. Run with:

    FULL_PYTHON_BASELINE_DATA=/path/to/9mo_bars.csv \
        PYTHONPATH=src python3 scripts/freeze_baseline_anchor.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from full_python.cli import run_baseline
from full_python.reporting.metrics import build_metrics_report

FROZEN_SIMULATION_OVERRIDES = {
    "point_value": 20.0,
    "commission_per_contract_round_trip": 10.0,
    "entry_slippage_points": 0.75,
    "exit_slippage_points": 0.75,
}


def freeze_baseline_anchor(
    *, data_path: Optional[Path], output_dir: Path
) -> Path:
    resolved_path = data_path
    if resolved_path is None:
        env_path = os.environ.get("FULL_PYTHON_BASELINE_DATA")
        if not env_path:
            raise ValueError(
                "No data_path given and FULL_PYTHON_BASELINE_DATA is not set. "
                "Point it at the 2025-10-01->2026-06-26 continuous NQ CSV "
                "(rebuild via `python -m full_python.data.databento` if missing)."
            )
        resolved_path = Path(env_path)

    report_path = run_baseline(
        data_path=resolved_path,
        output_dir=output_dir,
        strategy_name="adaptive_trend_am",
        simulation_overrides=dict(FROZEN_SIMULATION_OVERRIDES),
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    trades_path = Path(report["trades_path"])
    trades = _load_trades_for_metrics(trades_path)
    metrics_report = build_metrics_report(
        trades, point_value=FROZEN_SIMULATION_OVERRIDES["point_value"]
    )
    report["metrics"] = metrics_report.to_dict()
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def _load_trades_for_metrics(trades_path: Path) -> list:
    import csv

    from full_python.models import Trade

    trades = []
    with trades_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            trades.append(
                Trade(
                    symbol=row["symbol"],
                    side=row["side"],
                    quantity=int(row["quantity"]),
                    entry_timestamp_utc=row["entry_timestamp_utc"],
                    entry_price=float(row["entry_price"]),
                    exit_timestamp_utc=row["exit_timestamp_utc"],
                    exit_price=float(row["exit_price"]),
                    exit_reason=row["exit_reason"],
                    stop_price=float(row["stop_price"]),
                    gross_points=float(row["gross_points"]),
                    gross_pnl=float(row["gross_pnl"]),
                    commission=float(row["commission"]),
                    net_pnl=float(row["net_pnl"]),
                    mfe_points=float(row["mfe_points"]),
                    mae_points=float(row["mae_points"]),
                    session_date=row["session_date"],
                    ambiguous_exit=row["ambiguous_exit"] == "True",
                )
            )
    return trades


if __name__ == "__main__":
    freeze_baseline_anchor(
        data_path=None,
        output_dir=Path("runs/baseline-anchor"),
    )
```

Add an empty `scripts/__init__.py` if one does not already exist, so `scripts.freeze_baseline_anchor` is importable in tests:

```bash
touch scripts/__init__.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_freeze_baseline_anchor.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the real freeze (requires the operator's local 9-month CSV)**

Run:
```bash
cd "/Users/sais/Documents/New Beginning/full-python"
FULL_PYTHON_BASELINE_DATA=/path/to/your/9mo_bars.csv PYTHONPATH=src python3 scripts/freeze_baseline_anchor.py
```
Expected: prints nothing (script has no `print`); confirm by inspecting `runs/baseline-anchor/report.json` — `run_id` has 4 segments, `metrics.expectancy.trade_count` is close to 120 (matches the TV-reconciled count for this window; note extras from before 2025-10-28 are in scope for the sim but out of TV's history per the reconciliation doc, so the sim trade count will be slightly higher than 120 — that's expected and should be called out in the anchor doc, not treated as a discrepancy).

- [ ] **Step 6: Write the decision doc**

```markdown
# Python Baseline Anchor — Frozen 2026-07-04

Replaces the missing 3-year TradingView export as the reference point for
all future Python-side comparisons (see the Python Reference Engine
Migration plan). This anchor is immutable once committed: a future change
is judged against these exact numbers, not re-frozen to make a comparison
look favorable.

## Explicit scope-down (read this first)

The original migration spec called for a 3-year canonical window. **No
3-year NQ continuous dataset exists in this repository or on this
branch.** The only reconciled-against-TradingView window available is
2025-10-01 -> 2026-06-26 (9 months, 260,681 bars). This anchor freezes on
that 9-month window, not 3 years. Any Gate 1 promotion-table row that
needs 3 years of history (year-by-year robustness, a 2023-01 train split)
is **not supportable by this anchor** and must be flagged as data-limited
wherever it is invoked (see Task 8's sizing-gate doc).

## Exact identity

- Config hash (strategy): `<paste strategy.parameter_hash from report.json>`
- Config hash (simulation): `<paste simulation.parameter_hash from report.json>`
- Data hash: `<paste data.manifest_hash from report.json>`
- Code hash: `<paste code_version from report.json>` (git SHA of `claude/m4-regime` at freeze time)
- Full `run_id`: `<paste run_id from report.json>`

## Cost model

`point_value=20, commission_rt=10, entry_slippage_points=0.75, exit_slippage_points=0.75` — mirrors the TV reconciliation runs in `docs/decisions/2026-07-03-first-tv-reconciliation.md`, not `SimulationConfig`'s MNQ-first defaults.

## Strategy

`adaptive_trend_am` (`production_am_config()`) — the production AM (1-4 contract escalation) + equity-based DLL ($1,000) stack, reconciled 106/106 against the TV AM/DLL export per `docs/decisions/2026-07-03-m2b-am-dll-reconciliation.md`. This is the config any promotion path would actually deploy, not the flat 1-contract core.

## Data window

2025-10-01 -> 2026-06-26, 260,681 bars, Databento GLBX continuous front-month (roll = expiry - 3 business days, holiday-aware). Note the sim's trade count over this window is not identical to the 120-trade TV-reconciled count: 9 sim trades before 2025-10-28 are outside TV's 1-minute chart history and are in scope for the Python-only anchor even though they were out of scope for the TV reconciliation.

## Canonical metrics (from `runs/baseline-anchor/report.json`, `metrics` key)

- Trade count: `<paste metrics.expectancy.trade_count>`
- Net P&L: `<paste survivability.net_pnl>`
- Win rate: `<paste metrics.expectancy.win_rate>`
- Expectancy per trade: `<paste metrics.expectancy.expectancy_dollars>`
- Avg / median R-multiple: `<paste metrics.expectancy.avg_r_multiple>` / `<paste metrics.expectancy.median_r_multiple>`
- Max win / loss streak: `<paste metrics.max_win_streak>` / `<paste metrics.max_loss_streak>`
- By-exit-reason: `<paste metrics.by_exit_reason>`

## Canonical artifacts

`runs/baseline-anchor/report.json`, `trades.csv`, `events.jsonl`, `daily_pnl.csv`, `report.html` (gitignored — reproducible from the identity block above plus the operator's copy of the 9-month CSV via `scripts/freeze_baseline_anchor.py`).
```

Fill in every `<paste ...>` placeholder from the actual `runs/baseline-anchor/report.json` produced in Step 5 before committing — this doc must contain real numbers, not placeholders, once committed.

- [ ] **Step 7: Run full suite**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: 113 passed

- [ ] **Step 8: Commit**

```bash
git add scripts/freeze_baseline_anchor.py scripts/__init__.py tests/test_freeze_baseline_anchor.py docs/decisions/2026-07-04-python-baseline-anchor.md
git commit -m "docs: freeze the Python baseline anchor on the 9-month reconciled window"
```

---

### Task 5: Parity Delta Report — decomposed, not aggregated

**Files:**
- Create: `src/full_python/parity_report.py`
- Modify: `src/full_python/reconcile.py` (extend `TvTrade` and `MatchedPair` with the fields needed for decomposition)
- Test: `tests/test_parity_report.py`
- Modify: `tests/test_reconcile.py` (existing fixture gains a `Net P&L USD` column already present in the TV export format per the module docstring — assert it now parses)
- Create: `docs/decisions/2026-07-04-parity-delta-report.md`

**Interfaces:**
- Consumes: `full_python.reconcile.{TvTrade, SimTrade, MatchedPair, ReconciliationReport, load_tv_trades, load_sim_trades, reconcile}` (existing, extended in this task).
- Produces: `ParityDeltaReport` (dataclass, `.to_dict()`), `build_parity_delta_report(report: ReconciliationReport) -> ParityDeltaReport`.

- [ ] **Step 1: Extend `TvTrade` and `MatchedPair`, write the failing tests for `reconcile.py`**

```python
# append to tests/test_reconcile.py
def test_tv_export_parses_net_pnl_column() -> None:
    trades = load_tv_trades(_write(Path("/tmp"), "tv_pnl.csv", TV_EXPORT))
    assert trades[0].net_pnl == -625.0
    assert trades[1].net_pnl == 1400.0


def test_matched_pair_carries_exit_time_delta_and_pnl_delta(tmp_path: Path) -> None:
    tv_trades = load_tv_trades(_write(tmp_path, "tv.csv", TV_EXPORT))
    sim_trades = load_sim_trades(_write(tmp_path, "trades.csv", SIM_TRADES))

    report = reconcile(tv_trades, sim_trades, tolerance_minutes=3.0)

    long_match = next(m for m in report.matches if m["side"] == "long")
    assert long_match["exit_time_delta_minutes"] == 0.0
    assert long_match["tv_exit_signal"] == "Stop Loss"
    # sim net_pnl (-65.5) - tv net_pnl (-625.0)
    assert long_match["net_pnl_delta"] == pytest.approx(-65.5 - (-625.0))
```

(Use `tmp_path` consistently, not `/tmp` directly — the existing file already imports `Path` and uses `_write(tmp_path, ...)`; match that pattern exactly and add `import pytest` at the top if not already present.)

- [ ] **Step 2: Run to verify failure**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_reconcile.py -v`
Expected: FAIL — `AttributeError`/`TypeError` on `net_pnl`, `exit_time_delta_minutes`, `tv_exit_signal`, `net_pnl_delta` not existing yet.

- [ ] **Step 3: Extend `reconcile.py`**

In `src/full_python/reconcile.py`, modify `TvTrade`:

```python
@dataclass(frozen=True)
class TvTrade:
    trade_number: str
    side: str
    entry_time: datetime
    entry_price: float
    entry_signal: str
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    exit_signal: str
    quantity: float
    net_pnl: Optional[float] = None
```

In `load_tv_trades`, add a column lookup and parse it on the exit leg (TV's "Net P&L USD" is reported once, on the exit row):

```python
        col_price = _find_column(fieldnames, "price")
        col_qty = _find_column(fieldnames, "qty", "size")
        try:
            col_pnl = _find_column(fieldnames, "net p&l", "net pnl")
        except ValueError:
            col_pnl = None
```

and inside the `elif "exit" in leg_type:` branch:

```python
            elif "exit" in leg_type:
                record.setdefault("side", side)
                record["exit_time"] = parsed_time
                record["exit_price"] = price
                record["exit_signal"] = str(row[col_signal]).strip()
                if col_pnl is not None:
                    raw_pnl = str(row[col_pnl]).replace(",", "").strip()
                    record["net_pnl"] = float(raw_pnl) if raw_pnl else None
```

and in the final `TvTrade(...)` construction:

```python
        trades.append(
            TvTrade(
                trade_number=number,
                side=record.get("side", ""),
                entry_time=record["entry_time"],
                entry_price=record["entry_price"],
                entry_signal=record.get("entry_signal", ""),
                exit_time=record.get("exit_time"),
                exit_price=record.get("exit_price"),
                exit_signal=record.get("exit_signal", ""),
                quantity=record.get("quantity", 1.0),
                net_pnl=record.get("net_pnl"),
            )
        )
```

Modify `MatchedPair`:

```python
@dataclass(frozen=True)
class MatchedPair:
    tv_trade_number: str
    side: str
    entry_time_delta_minutes: float
    entry_price_delta: float
    exit_price_delta: Optional[float]
    exit_time_delta_minutes: Optional[float]
    tv_entry_signal: str
    tv_exit_signal: str
    sim_exit_reason: str
    tv_net_pnl: Optional[float]
    sim_net_pnl: Optional[float]
    net_pnl_delta: Optional[float]
    tv_quantity: float = 1.0
    sim_quantity: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

`SimTrade` needs `net_pnl` too, to compute `net_pnl_delta`:

```python
@dataclass(frozen=True)
class SimTrade:
    side: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    quantity: float
    net_pnl: Optional[float] = None
```

`load_sim_trades` reads it if the column exists (the existing `trades.csv` schema in `cli.py:TRADE_CSV_COLUMNS` already writes `net_pnl`):

```python
def load_sim_trades(path: str | Path) -> list[SimTrade]:
    trades = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            trades.append(
                SimTrade(
                    side=row["side"],
                    entry_time=parse_timestamp_utc(row["entry_timestamp_utc"]),
                    entry_price=float(row["entry_price"]),
                    exit_time=parse_timestamp_utc(row["exit_timestamp_utc"]),
                    exit_price=float(row["exit_price"]),
                    exit_reason=row["exit_reason"],
                    quantity=float(row["quantity"]),
                    net_pnl=float(row["net_pnl"]) if "net_pnl" in row and row["net_pnl"] else None,
                )
            )
    trades.sort(key=lambda trade: trade.entry_time)
    return trades
```

Finally, in `reconcile()`, extend the `MatchedPair` construction:

```python
        unmatched_sim.remove(best)
        exit_delta = (
            best.exit_price - tv_trade.exit_price if tv_trade.exit_price is not None else None
        )
        exit_time_delta = (
            abs((best.exit_time - tv_trade.exit_time).total_seconds() / 60.0)
            if tv_trade.exit_time is not None
            else None
        )
        net_pnl_delta = (
            best.net_pnl - tv_trade.net_pnl
            if best.net_pnl is not None and tv_trade.net_pnl is not None
            else None
        )
        matches.append(
            MatchedPair(
                tv_trade_number=tv_trade.trade_number,
                side=tv_trade.side,
                entry_time_delta_minutes=best_delta if best_delta is not None else 0.0,
                entry_price_delta=best.entry_price - tv_trade.entry_price,
                exit_price_delta=exit_delta,
                exit_time_delta_minutes=exit_time_delta,
                tv_entry_signal=tv_trade.entry_signal,
                tv_exit_signal=tv_trade.exit_signal,
                sim_exit_reason=best.exit_reason,
                tv_net_pnl=tv_trade.net_pnl,
                sim_net_pnl=best.net_pnl,
                net_pnl_delta=net_pnl_delta,
                tv_quantity=tv_trade.quantity,
                sim_quantity=best.quantity,
            ).to_dict()
        )
```

- [ ] **Step 4: Run to verify `reconcile.py` tests pass**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_reconcile.py -v`
Expected: all pass (existing 3 tests + 2 new)

- [ ] **Step 5: Write the failing test for `parity_report.py`**

```python
# tests/test_parity_report.py
from pathlib import Path

from full_python.parity_report import build_parity_delta_report
from full_python.reconcile import load_sim_trades, load_tv_trades, reconcile

TV_EXPORT = (
    "﻿Trade #,Type,Date and time,Signal,Price USD,Size (qty),Size (value),Net P&L USD\n"
    "1,Entry long,2026-06-30 09:33,Long,20105.25,1,402105,0\n"
    "1,Exit long,2026-06-30 09:41,Stop Loss,20074.25,1,401485,-625\n"
    "2,Entry short,2026-06-30 09:52,Short,20080.50,1,401610,0\n"
    "2,Exit short,2026-06-30 10:31,ATF Flip,20010.00,1,400200,1400\n"
)
SIM_TRADES = (
    "symbol,side,quantity,entry_timestamp_utc,entry_price,exit_timestamp_utc,exit_price,"
    "exit_reason,stop_price,gross_points,gross_pnl,commission,net_pnl,mfe_points,mae_points,"
    "session_date,ambiguous_exit\n"
    "NQU2026,long,1,2026-06-30T13:34:00Z,20106.25,2026-06-30T13:41:00Z,20074.25,stop,20074.25,"
    "-32.0,-64.0,1.0,-65.0,3.0,32.5,2026-06-30,False\n"
    "NQU2026,short,1,2026-06-30T13:52:00Z,20080.5,2026-06-30T14:31:00Z,20010.0,atf_flip,20110.5,"
    "70.5,141.0,1.0,140.0,70.0,4.0,2026-06-30,False\n"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_parity_delta_report_decomposes_entry_and_exit_checks(tmp_path: Path) -> None:
    tv_trades = load_tv_trades(_write(tmp_path, "tv.csv", TV_EXPORT))
    sim_trades = load_sim_trades(_write(tmp_path, "trades.csv", SIM_TRADES))
    reconciliation = reconcile(tv_trades, sim_trades, tolerance_minutes=3.0)

    parity = build_parity_delta_report(reconciliation)

    assert parity.trade_count_exact is True  # 2 TV, 2 sim, 2 matched, 0 missing, 0 extra
    assert parity.entry_timestamp_exact_count == 2
    assert parity.entry_price_exact_count == 2  # both deltas are 0.0
    assert parity.exit_reason_exact_count == 1  # "stop" vs "Stop Loss" differ in spelling; "atf_flip" vs "ATF Flip" differ too -- both are case/label mismatches, not logic mismatches, and must be surfaced, not silently normalized
    assert parity.max_abs_exit_price_delta == 0.0
    assert len(parity.largest_pnl_deltas) == 2
    assert parity.largest_pnl_deltas[0]["tv_trade_number"] in ("1", "2")


def test_parity_delta_report_flags_trade_count_mismatch() -> None:
    from full_python.reconcile import ReconciliationReport

    reconciliation = ReconciliationReport(
        tv_trade_count=3, sim_trade_count=2, matched_count=2,
        missing_in_sim=[{"tv_trade_number": "3"}],
    )

    parity = build_parity_delta_report(reconciliation)

    assert parity.trade_count_exact is False
```

- [ ] **Step 6: Run to verify failure**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_parity_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'full_python.parity_report'`

- [ ] **Step 7: Implement**

```python
# src/full_python/parity_report.py
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
```

Note: `exit_reason_exact` in this implementation applies a documented lowercase/underscore normalization (`"Stop Loss"` -> `"stop_loss"`) purely so the loose spelling difference doesn't drown out real mismatches — but `"stop_loss"` still won't equal the sim's `"stop"`, so it will legitimately report as inexact unless the caller decides `"Stop Loss" == "stop"` is an intentional equivalence. Do not weaken the normalization further to force a match; if the test above (`exit_reason_exact_count == 1`) doesn't hold with this exact normalization, that's the module correctly refusing to paper over the label difference — verify by hand which of the two TV exit signals happens to normalize to match its sim counterpart, and adjust the test's expected count to the true number, not the module's normalization.

- [ ] **Step 8: Run tests, adjust exact expected counts from real output**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_parity_report.py -v`
Expected: if `exit_reason_exact_count` assertion fails, print `parity.exit_reason_exact_count` and the raw `tv_exit_signal`/`sim_exit_reason` pairs, fix the test's expected number to match reality (do not change the module to force the test's originally-guessed number).

- [ ] **Step 9: Run full suite**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: all prior tests + new ones pass, no regressions

- [ ] **Step 10: Produce and write the decision doc against the frozen anchor**

Run:
```bash
cd "/Users/sais/Documents/New Beginning/full-python"
PYTHONPATH=src python3 -c "
import json
from full_python.parity_report import build_parity_delta_report
from full_python.reconcile import load_sim_trades, load_tv_trades, reconcile

tv = load_tv_trades('/path/to/AT-RSRCH_..._9e40f.csv')
sim = load_sim_trades('runs/baseline-anchor/trades.csv')
report = reconcile(tv, sim, tolerance_minutes=3.0)
parity = build_parity_delta_report(report)
print(json.dumps(parity.to_dict(), indent=2, default=str))
"
```

Write `docs/decisions/2026-07-04-parity-delta-report.md` with the printed numbers filled in against the required-checks table (entry timestamp, entry price, exit timestamp, exit reason, exit price, trade count, P&L delta, largest-20 review) plus a manual, by-hand review of each of the `largest_pnl_deltas` entries (the spec requires this be manual, not automated — do not skip it or replace it with another automated pass).

- [ ] **Step 11: Commit**

```bash
git add src/full_python/reconcile.py src/full_python/parity_report.py tests/test_reconcile.py tests/test_parity_report.py docs/decisions/2026-07-04-parity-delta-report.md
git commit -m "feat: add decomposed parity delta report on top of TV reconciliation"
```

---

### Task 6: Golden-trade regression suite

**Files:**
- Create: `tests/test_golden_trades.py`
- Create: `tests/fixtures/golden_trades.json`
- Create: `scripts/export_golden_trades.py`

**Interfaces:**
- Consumes: `full_python.simulation.SimulationConfig`, `SimulationEngine`; `full_python.strategy.adaptive_trend_config.production_am_config`; `full_python.strategy.adaptive_trend.AdaptiveTrendStrategy`; `full_python.data.loaders.load_csv_bars`, `CsvBarColumnMap`.
- Produces: a byte-identical-on-rerun regression fixture proving the frozen anchor's trade sequence never silently drifts — this is what later protects the Task 7 risk-layer refactor.

The 120 TV-reconciled trades live in the operator's local, gitignored 9-month CSV — there is no `runs/nq1_continuous_3yr.csv` to point at (confirmed absent repo-wide). This task exports the frozen anchor's own trade ledger (already produced in Task 4) into a small, committed JSON fixture, so the suite runs in CI without the 9-month CSV present. If `FULL_PYTHON_BASELINE_DATA` is set, an additional live test re-derives the trades from the CSV and asserts they still match the committed fixture — this is what actually catches drift; the fixture-only test just guards against accidental fixture corruption.

- [ ] **Step 1: Write the export script**

```python
# scripts/export_golden_trades.py
"""Serialize the frozen anchor's trade ledger into tests/fixtures/golden_trades.json.

Run once, by hand, after Task 4's freeze produces runs/baseline-anchor/trades.csv:

    PYTHONPATH=src python3 scripts/export_golden_trades.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

TRADES_CSV = Path("runs/baseline-anchor/trades.csv")
FIXTURE_PATH = Path("tests/fixtures/golden_trades.json")


def export_golden_trades() -> None:
    with TRADES_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    export_golden_trades()
```

- [ ] **Step 2: Run the export against the real freeze**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && PYTHONPATH=src python3 scripts/export_golden_trades.py`
Expected: `tests/fixtures/golden_trades.json` created with ~120+ trade rows (see Task 4 Step 5's note on why the count is slightly above 120)

- [ ] **Step 3: Write the failing tests**

```python
# tests/test_golden_trades.py
import csv
import json
import os
from pathlib import Path

import pytest

from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import production_am_config

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_trades.json"
FROZEN_SIMULATION_OVERRIDES = {
    "point_value": 20.0,
    "commission_per_contract_round_trip": 10.0,
    "entry_slippage_points": 0.75,
    "exit_slippage_points": 0.75,
}


def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_golden_trade_fixture_exists_and_is_nonempty() -> None:
    trades = _load_fixture()
    assert len(trades) > 0
    assert "entry_price" in trades[0]
    assert "exit_reason" in trades[0]


def test_golden_trade_fixture_am_quantities_show_the_reconciled_escalation() -> None:
    # docs/decisions/2026-07-03-m2b-am-dll-reconciliation.md: 103x(1,1), 1x(2,2), 2x(3,3)
    trades = _load_fixture()
    quantities = [int(t["quantity"]) for t in trades if t["exit_reason"] != "session_end"]
    assert max(quantities) >= 2  # AM did escalate at least once in the frozen window


@pytest.mark.skipif(
    "FULL_PYTHON_BASELINE_DATA" not in os.environ,
    reason="requires the operator's local 9-month CSV; set FULL_PYTHON_BASELINE_DATA to run",
)
def test_replaying_the_frozen_window_reproduces_the_golden_fixture_exactly() -> None:
    column_map = CsvBarColumnMap(
        timestamp="timestamp", symbol="symbol", open="open",
        high="high", low="low", close="close", volume="volume",
    )
    bars = load_csv_bars(Path(os.environ["FULL_PYTHON_BASELINE_DATA"]), column_map)
    config = production_am_config()
    strategy = AdaptiveTrendStrategy(config)
    simulation_config = SimulationConfig(**FROZEN_SIMULATION_OVERRIDES)
    result = SimulationEngine(simulation_config).run(bars, strategy)

    replayed = [trade.to_payload() for trade in result.trades]
    golden = _load_fixture()

    assert len(replayed) == len(golden)
    for replayed_trade, golden_trade in zip(replayed, golden):
        assert replayed_trade["entry_timestamp_utc"] == golden_trade["entry_timestamp_utc"]
        assert replayed_trade["exit_timestamp_utc"] == golden_trade["exit_timestamp_utc"]
        assert replayed_trade["exit_reason"] == golden_trade["exit_reason"]
        assert replayed_trade["entry_price"] == pytest.approx(float(golden_trade["entry_price"]))
        assert replayed_trade["exit_price"] == pytest.approx(float(golden_trade["exit_price"]))
        assert replayed_trade["net_pnl"] == pytest.approx(float(golden_trade["net_pnl"]))
```

- [ ] **Step 4: Run tests to verify the fixture-based tests pass and the live test skips cleanly**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_golden_trades.py -v`
Expected: 2 passed, 1 skipped (unless `FULL_PYTHON_BASELINE_DATA` happens to be set in this shell, in which case 3 passed)

- [ ] **Step 5: Run the live test at least once with the real data present**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && FULL_PYTHON_BASELINE_DATA=/path/to/your/9mo_bars.csv python3 -m pytest tests/test_golden_trades.py -v`
Expected: 3 passed — this is the run that actually proves determinism; do not consider Task 6 done until this has been run and passed at least once, even though CI will only see the skipped version

- [ ] **Step 6: Run full suite**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: all pass, 2 new (3 when the env var is set)

- [ ] **Step 7: Commit**

```bash
git add scripts/export_golden_trades.py tests/test_golden_trades.py tests/fixtures/golden_trades.json
git commit -m "feat: add golden-trade regression suite from the frozen baseline anchor"
```

---

### Task 7: Extract the shared `risk/` layer

**Files:**
- Create: `src/full_python/risk/__init__.py`
- Create: `src/full_python/risk/session_rules.py`
- Create: `src/full_python/risk/position_limits.py`
- Create: `src/full_python/risk/daily_loss.py`
- Create: `src/full_python/risk/risk_manager.py`
- Modify: `src/full_python/simulation/engine.py`
- Create: `tests/test_risk_manager.py`

**Interfaces:**
- Consumes: `full_python.data.sessions.SessionInfo`, `full_python.models.OrderIntent`, `full_python.simulation.config.SimulationConfig` (all existing, unchanged).
- Produces: `evaluate_session_window(session, config) -> Optional[str]`, `evaluate_position_limits(state_has_open_order: bool, quantity: int, max_contracts: int) -> Optional[str]`, `evaluate_daily_loss(session_pnl: float, limit: Optional[float], already_hit: bool) -> bool`, `RiskManager.veto_reason(*, has_open_order, daily_limit_hit, session, intent, max_contracts, flatten_minutes_et, rth_entries_only, reference_price) -> Optional[str]` — this is the exact behavior of today's `SimulationEngine._veto_reason` (engine.py:446-472), moved verbatim, not rewritten. **This is the critical constraint: do not "clean up" or reorder the veto checks while moving them — Task 6's golden-trade suite exists specifically to catch any behavior change, intentional or not, and a reordering of independent-looking checks can still change which veto reason fires first for a given intent.**

- [ ] **Step 1: Write the failing tests for the extracted modules, driving out the exact same behavior as today's `_veto_reason`**

```python
# tests/test_risk_manager.py
from full_python.data.sessions import classify_timestamp
from full_python.models import OrderIntent
from full_python.risk.risk_manager import RiskManager
from full_python.simulation.config import SimulationConfig


def _intent(*, side: str = "buy", quantity: int = 1, stop_price: float | None = 95.0) -> OrderIntent:
    metadata = {} if stop_price is None else {"stop_price": stop_price, "signal_price": 100.0}
    return OrderIntent.market_entry(
        timestamp_utc="2026-01-05T14:30:00Z",
        symbol="NQU2026",
        side=side,
        quantity=quantity,
        reason="test",
        metadata=metadata,
    )


def test_veto_reason_none_for_a_valid_intent_during_rth() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")  # RTH open window
    manager = RiskManager(SimulationConfig())

    result = manager.veto_reason(
        has_open_order=False,
        daily_limit_hit=False,
        session=session,
        intent=_intent(),
        reference_price=100.0,
    )

    assert result is None


def test_veto_reason_invalid_side() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")
    manager = RiskManager(SimulationConfig())
    bad_intent = _intent()
    object.__setattr__(bad_intent, "side", "hold")

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=False, session=session,
        intent=bad_intent, reference_price=100.0,
    )

    assert result == "invalid_side"


def test_veto_reason_invalid_quantity_over_max_contracts() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")
    manager = RiskManager(SimulationConfig(max_contracts=2))

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=False, session=session,
        intent=_intent(quantity=3), reference_price=100.0,
    )

    assert result == "invalid_quantity"


def test_veto_reason_position_already_open() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")
    manager = RiskManager(SimulationConfig())

    result = manager.veto_reason(
        has_open_order=True, daily_limit_hit=False, session=session,
        intent=_intent(), reference_price=100.0,
    )

    assert result == "position_already_open"


def test_veto_reason_daily_limit_hit_takes_priority_over_rth_check() -> None:
    session = classify_timestamp("2026-01-05T02:00:00Z")  # outside RTH AND daily limit hit
    manager = RiskManager(SimulationConfig())

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=True, session=session,
        intent=_intent(), reference_price=100.0,
    )

    assert result == "daily_limit"  # matches today's check order in engine.py:459-464


def test_veto_reason_outside_rth() -> None:
    session = classify_timestamp("2026-01-05T02:00:00Z")
    manager = RiskManager(SimulationConfig(rth_entries_only=True))

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=False, session=session,
        intent=_intent(), reference_price=100.0,
    )

    assert result == "outside_rth"


def test_veto_reason_missing_stop() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")
    manager = RiskManager(SimulationConfig())

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=False, session=session,
        intent=_intent(stop_price=None), reference_price=100.0,
    )

    assert result == "missing_stop"


def test_veto_reason_invalid_stop_for_buy_above_reference() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")
    manager = RiskManager(SimulationConfig())

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=False, session=session,
        intent=_intent(stop_price=105.0), reference_price=100.0,
    )

    assert result == "invalid_stop"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_risk_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'full_python.risk'`

- [ ] **Step 3: Implement the extracted modules — copy the exact logic, do not alter check order**

```python
# src/full_python/risk/__init__.py
from full_python.risk.risk_manager import RiskManager

__all__ = ["RiskManager"]
```

```python
# src/full_python/risk/session_rules.py
"""RTH-window and after-flatten checks, extracted verbatim from
SimulationEngine._veto_reason (see docs/decisions/2026-07-03-fill-simulation-policy.md
for the per-bar order these checks participate in).
"""
from __future__ import annotations

from typing import Optional

from full_python.data.sessions import SessionInfo


def check_after_flatten(session: SessionInfo, flatten_minutes_et: int) -> Optional[str]:
    if session.minutes_from_midnight_et >= flatten_minutes_et:
        return "after_flatten"
    return None


def check_rth_window(session: SessionInfo, rth_entries_only: bool) -> Optional[str]:
    if rth_entries_only and not session.is_rth:
        return "outside_rth"
    return None
```

```python
# src/full_python/risk/position_limits.py
"""Quantity and one-position-at-a-time checks, extracted verbatim from
SimulationEngine._veto_reason.
"""
from __future__ import annotations

from typing import Optional


def check_quantity(quantity: int, max_contracts: int) -> Optional[str]:
    if quantity < 1 or quantity > max_contracts:
        return "invalid_quantity"
    return None


def check_no_open_order(has_open_order: bool) -> Optional[str]:
    if has_open_order:
        return "position_already_open"
    return None
```

```python
# src/full_python/risk/daily_loss.py
"""Equity-based daily-loss-limit halt check, extracted verbatim from
SimulationEngine._veto_reason and _check_daily_loss_limit.
"""
from __future__ import annotations

from typing import Optional


def check_daily_limit_halt(daily_limit_hit: bool) -> Optional[str]:
    if daily_limit_hit:
        return "daily_limit"
    return None


def is_daily_loss_breached(session_pnl: float, daily_loss_limit: Optional[float]) -> bool:
    """Matches engine.py:313 exactly: breach when session_pnl <= -limit."""
    if daily_loss_limit is None:
        return False
    return session_pnl <= -daily_loss_limit
```

```python
# src/full_python/risk/risk_manager.py
"""Shared risk-veto gate for a proposed order intent.

Behavior-preserving extraction from SimulationEngine._veto_reason
(simulation/engine.py:446-472) -- the exact same checks, in the exact
same order, so SimulationEngine's refactor to call this module is proven
unchanged by tests/test_golden_trades.py passing identically before and
after. Any live BrokerExecutionEngine (future work, Gate 5+) calls this
same module, never simulation-internal code.
"""
from __future__ import annotations

from typing import Optional

from full_python.data.sessions import SessionInfo
from full_python.models import OrderIntent
from full_python.risk.daily_loss import check_daily_limit_halt
from full_python.risk.position_limits import check_no_open_order, check_quantity
from full_python.risk.session_rules import check_after_flatten, check_rth_window
from full_python.simulation.config import SimulationConfig


class RiskManager:
    def __init__(self, config: SimulationConfig) -> None:
        self.config = config

    def veto_reason(
        self,
        *,
        has_open_order: bool,
        daily_limit_hit: bool,
        session: SessionInfo,
        intent: OrderIntent,
        reference_price: float,
    ) -> Optional[str]:
        if intent.side not in ("buy", "sell"):
            return "invalid_side"

        reason = check_quantity(intent.quantity, self.config.max_contracts)
        if reason is not None:
            return reason

        reason = check_no_open_order(has_open_order)
        if reason is not None:
            return reason

        reason = check_daily_limit_halt(daily_limit_hit)
        if reason is not None:
            return reason

        reason = check_after_flatten(session, self.config.flatten_minutes_et)
        if reason is not None:
            return reason

        reason = check_rth_window(session, self.config.rth_entries_only)
        if reason is not None:
            return reason

        if "stop_price" not in intent.metadata:
            return "missing_stop"

        stop_price = float(intent.metadata["stop_price"])
        if intent.side == "buy" and stop_price >= reference_price:
            return "invalid_stop"
        if intent.side == "sell" and stop_price <= reference_price:
            return "invalid_stop"
        return None
```

- [ ] **Step 4: Run the new tests to verify they pass in isolation**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_risk_manager.py -v`
Expected: 8 passed

- [ ] **Step 5: Refactor `SimulationEngine` to delegate to `RiskManager`, changing nothing else**

In `src/full_python/simulation/engine.py`, add the import:

```python
from full_python.risk.risk_manager import RiskManager
```

In `SimulationEngine.__init__`, construct the manager once:

```python
class SimulationEngine:
    def __init__(self, config: SimulationConfig) -> None:
        self.config = config
        self._risk_manager = RiskManager(config)
```

Replace the entire body of `_veto_reason` with a delegation, keeping the method (and its call sites) unchanged so nothing else in the file needs to move:

```python
    def _veto_reason(
        self, state: _State, session: SessionInfo, intent: OrderIntent
    ) -> Optional[str]:
        return self._risk_manager.veto_reason(
            has_open_order=(
                state.position is not None
                or state.pending_entry is not None
                or state.pending_exit is not None
            ),
            daily_limit_hit=state.daily_limit_hit,
            session=session,
            intent=intent,
            reference_price=self._reference_price(state, intent),
        )
```

`_check_daily_loss_limit` (engine.py:290-332) stays in `SimulationEngine` as-is for this task — it mutates `state` and appends ledger events directly, which is engine-specific orchestration, not a pure risk-decision function. Only the pure breach-check arithmetic is worth extracting; wire it in without changing behavior:

```python
    def _check_daily_loss_limit(
        self, state: _State, bar: MarketBar, ledger: EventLedger
    ) -> float:
        unrealized = 0.0
        position = state.position
        if position is not None:
            unrealized = (
                (bar.close - position.entry_price)
                * position.direction
                * self.config.point_value
                * position.quantity
            )
        session_pnl = state.cumulative_net_pnl - state.session_start_pnl + unrealized
        if state.daily_limit_hit:
            return session_pnl
        from full_python.risk.daily_loss import is_daily_loss_breached

        if is_daily_loss_breached(session_pnl, self.config.daily_loss_limit):
            state.daily_limit_hit = True
            ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=bar.timestamp_utc,
                payload={
                    "transition": "daily_limit_hit",
                    "session_pnl": session_pnl,
                    "daily_loss_limit": self.config.daily_loss_limit,
                },
            )
            if position is not None:
                position.stop_cancelled = True
                if state.pending_exit is None:
                    state.pending_exit = _PendingExit(
                        reason="daily_limit", timestamp_utc=bar.timestamp_utc
                    )
                else:
                    state.pending_exit.reason = "daily_limit"
        return session_pnl
```

(Move the `from full_python.risk.daily_loss import is_daily_loss_breached` import to the top-level import block alongside the `RiskManager` import instead of inline — inline shown here only to make the diff obvious; do not leave it as a function-local import in the final code.)

- [ ] **Step 6: Run the full existing simulation-engine test file to confirm behavior is unchanged**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_simulation_engine.py tests/test_am_dll.py -v`
Expected: all pass, unchanged from before the refactor

- [ ] **Step 7: Run the golden-trade suite — this is the actual proof the refactor is behavior-preserving**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && FULL_PYTHON_BASELINE_DATA=/path/to/your/9mo_bars.csv python3 -m pytest tests/test_golden_trades.py -v`
Expected: 3 passed, identical to Task 6 Step 5's result — if anything here fails, the refactor changed behavior; find and fix the discrepancy before proceeding, do not adjust the golden fixture to match the new output (that would defeat the entire point of Task 6)

- [ ] **Step 8: Run full suite**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: all pass, no regressions

- [ ] **Step 9: Commit**

```bash
git add src/full_python/risk/ src/full_python/simulation/engine.py tests/test_risk_manager.py
git commit -m "refactor: extract shared risk/ layer from SimulationEngine, behavior-preserving"
```

---

### Task 8: Capital Allocation / Sizing Research Gate (data-limited)

**Files:**
- Create: `docs/decisions/2026-07-04-sizing-research-gate.md`
- Create: `tests/test_sizing_candidates.py` (documents the candidates as executable comparisons, not just prose)

**Interfaces:**
- Consumes: `full_python.cli.run_baseline`, `full_python.reporting.metrics.build_metrics_report`, `full_python.reporting.survivability.build_survivability_report` (all existing/Task 1-3 additions). No new production code — this task runs comparisons and writes findings.

This gate is explicitly scoped down per the Global Constraints data-span decision: n=120 trades over 9 months cannot support the full Gate 1 promotion table (train/holdout split, top-1/2/3-trade-removal robustness, year-by-year robustness all require far more trades/history than exist here). This task runs the mechanically comparable candidates only and states the limitation in the doc rather than presenting a thin result as a full promotion decision.

- [ ] **Step 1: Write the candidate-comparison test**

```python
# tests/test_sizing_candidates.py
import os
from pathlib import Path

import pytest

from full_python.cli import run_baseline
from full_python.reporting.metrics import build_metrics_report

FROZEN_SIMULATION_OVERRIDES_1NQ = {
    "point_value": 20.0,
    "commission_per_contract_round_trip": 10.0,
    "entry_slippage_points": 0.75,
    "exit_slippage_points": 0.75,
}
# MNQ = 1/10th the point value of NQ (2.0 vs 20.0), same tick/contract logic.
FROZEN_SIMULATION_OVERRIDES_1MNQ = {
    "point_value": 2.0,
    "commission_per_contract_round_trip": 1.0,
    "entry_slippage_points": 0.75,
    "exit_slippage_points": 0.75,
}


@pytest.mark.skipif(
    "FULL_PYTHON_BASELINE_DATA" not in os.environ,
    reason="requires the operator's local 9-month CSV; set FULL_PYTHON_BASELINE_DATA to run",
)
def test_1nq_vs_1mnq_sizing_comparison_on_the_frozen_window(tmp_path: Path) -> None:
    data_path = Path(os.environ["FULL_PYTHON_BASELINE_DATA"])

    nq_report_path = run_baseline(
        data_path=data_path,
        output_dir=tmp_path / "1nq",
        strategy_name="adaptive_trend_am",
        simulation_overrides=dict(FROZEN_SIMULATION_OVERRIDES_1NQ),
    )
    mnq_report_path = run_baseline(
        data_path=data_path,
        output_dir=tmp_path / "1mnq",
        strategy_name="adaptive_trend_am",
        simulation_overrides=dict(FROZEN_SIMULATION_OVERRIDES_1MNQ),
    )

    import json

    nq_report = json.loads(nq_report_path.read_text(encoding="utf-8"))
    mnq_report = json.loads(mnq_report_path.read_text(encoding="utf-8"))

    # Same signal core, same trade timing -- only point value/commission differ,
    # so trade COUNT must be identical; only P&L scales.
    assert nq_report["survivability"]["trade_count"] == mnq_report["survivability"]["trade_count"]
    # NQ P&L should be ~10x MNQ P&L (10x point value, 10x commission) minus the
    # commission-per-trade difference; assert the ratio is in a sane band rather
    # than an exact 10x (small-quantity rounding and AM-escalation trades make
    # exact 10x unlikely).
    if mnq_report["survivability"]["net_pnl"] != 0:
        ratio = nq_report["survivability"]["net_pnl"] / mnq_report["survivability"]["net_pnl"]
        assert 8.0 < ratio < 12.0
```

- [ ] **Step 2: Run it with the real data**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && FULL_PYTHON_BASELINE_DATA=/path/to/your/9mo_bars.csv python3 -m pytest tests/test_sizing_candidates.py -v`
Expected: 1 passed (or a clear, printed ratio if the assertion needs adjusting — record the true ratio in the decision doc either way)

- [ ] **Step 3: Run without the env var to confirm the skip path is clean for CI**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_sizing_candidates.py -v`
Expected: 1 skipped

- [ ] **Step 4: Write the decision doc**

```markdown
# Capital Allocation / Sizing Research Gate — 2026-07-04 (data-limited)

## Scope caveat (binding, do not drop in any future reference to this doc)

This gate runs on the frozen 9-month Python Baseline Anchor
(docs/decisions/2026-07-04-python-baseline-anchor.md), n=<paste trade
count>. The full spec's Gate 1 promotion table requires a train/holdout
split (2023-01->2025-06 / 2025-07->2026-06) and top-1/2/3-trade-removal
+ year-by-year robustness checks. **None of those are statistically
meaningful at n=<paste trade count> over 9 months.** This document
reports mechanically comparable candidates only; it is not a promotion
decision, and no candidate here should be treated as cleared for live
deployment on this evidence alone. Re-run this exact comparison once a
multi-year dataset exists.

## Candidate 1: 1 NQ vs 1 MNQ (same signal core, point-value/commission only)

- 1 NQ: point_value=20, commission_rt=10, net P&L = <paste>
- 1 MNQ: point_value=2, commission_rt=1, net P&L = <paste>
- Trade count identical (same signal timing, confirmed): <paste count> both sides
- Ratio: <paste> (expected ~10x; commission is a smaller fraction of NQ's larger
  per-trade P&L, so the ratio is not exactly 10x)

## Candidates not run in this pass (require more history or live infrastructure)

- Volatility-scaled sizing, drawdown-based de-risking: need enough trades per
  volatility/drawdown bucket to be meaningful; 9 months does not provide it.
- Anti-martingale (already built, already in the frozen anchor via
  `production_am_config()` -- not a new candidate to test, it's the baseline).
- Prop-firm consistency-cap compliance, daily-stop interaction,
  account-size-specific deployment: these are policy constraints to check the
  existing frozen anchor against, not sizing models to sweep; do as a
  follow-up pass reading `docs/decisions/2026-07-04-python-baseline-anchor.md`'s
  daily P&L series against each target account's consistency-cap rule.

## Conclusion

1 NQ vs 1 MNQ is the only candidate in this list that is purely mechanical
(same trades, different multiplier) and therefore safe to compare even at
n=<paste trade count>. Everything else on the spec's candidate list needs
either more history or a running paper/live system and is deferred, not
rejected.
```

Fill in every `<paste ...>` from the real run's numbers before committing.

- [ ] **Step 5: Run full suite**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: all pass (1 new test, skips without the env var)

- [ ] **Step 6: Commit**

```bash
git add tests/test_sizing_candidates.py docs/decisions/2026-07-04-sizing-research-gate.md
git commit -m "docs: run the data-limited sizing research gate on the frozen anchor"
```

---

## Self-Review Notes

- **Spec coverage:** Steps 1 (Task 4), 2 (Task 5), 3 (Tasks 1-2), 4 (Task 6), 5 (Task 7), 6 (Task 3), 7 (Task 8) of the spec's sequencing are all covered. Steps 8-11 (live data, broker adapter, failure matrix, Gates 5-7) are explicitly out of scope per the spec's own text ("Steps 8-11 are the natural point to re-scope this plan with file-level detail, once 1-7 produce real findings").
- **Data-span gap:** surfaced explicitly in Global Constraints and repeated in Tasks 4 and 8's decision docs rather than silently assumed away.
- **No placeholders in code steps:** every code block is complete and runnable against the actual current file contents (verified via direct reads of `models.py`, `reconcile.py`, `simulation/engine.py`, `events.py`, `cli.py`, `simulation/config.py`, `reporting/survivability.py` on `claude/m4-regime` tip `62cbd20`); the only bracketed placeholders are `<paste ...>` values that come from the operator's own machine-specific run and cannot be known until Task 4/5/8 are actually executed against the local 9-month CSV.
- **Type/signature consistency:** `Trade`, `SimulationConfig`, `SimulationResult`, `EventLedger`, `RiskManager.veto_reason` signatures are used identically across every task that references them.
