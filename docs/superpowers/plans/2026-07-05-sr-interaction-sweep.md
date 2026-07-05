# S/R Interaction Sweep Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pre-registered driver (`scripts/sweep_sr_interaction.py`) for the `sr_min_stop_distance` × `sr_stop_buffer` interaction sweep — the last open Gate 1 Phase 4 axis — plus a test pinning its grid literals.

**Architecture:** Deliberate clone of `scripts/sweep_ma_lengths.py` (approved Approach A: the MA driver is retired, this is the final sweep, and the must-stay-identical scoring logic already lives in `src/full_python/research/sweep.py`). Only the grid literals, override keys, output directory, cell naming, and column headers change.

**Tech Stack:** Python 3 stdlib; existing `full_python.research.sweep` (`run_grid`, `score_cell`, `select_qualifier`), `full_python.cli.TRADE_CSV_COLUMNS`, `full_python.data.loaders`, `scripts.freeze_baseline_anchor.FROZEN_SIMULATION_OVERRIDES`, `production_am_config()`.

## Global Constraints

- The pre-registered grid is exactly `sr_min_stop_distance ∈ {10.0, 12.0, 15.0, 18.0, 20.0}` × `sr_stop_buffer ∈ {3.0, 5.0, 7.0, 9.0}` (20 cells, baseline (15.0, 5.0) as the empty override dict `{}`). Values are FLOATS — both config fields are `float`, and an int override would change `parameter_hash` semantics without changing behavior. No other values, no adaptive refinement.
- No changes to `src/full_python/research/sweep.py`, `scripts/sweep_ma_lengths.py`, or any `src/full_python` production module (`strategy/`, `simulation/`, `risk/`, `regime.py`, `cli.py`).
- Cost model from `scripts.freeze_baseline_anchor.FROZEN_SIMULATION_OVERRIDES` — never re-typed literals.
- The driver never runs holdout and never runs slippage variants (row 8).
- `python3 -m pytest -q` stays green. Baseline before this task: 154 passed, 2 skipped. Expected after: 155 passed, 2 skipped.
- Commit style `feat: ...`.

---

### Task 1: the S/R interaction sweep driver and its pinning test

**Files:**
- Create: `scripts/sweep_sr_interaction.py`
- Create: `tests/test_sweep_sr_driver.py`

**Interfaces:**
- Consumes: `run_grid(bars, base_config, overrides_list, sim_config, train_start, train_end) -> list[CellResult]`, `score_cell(cell, baseline) -> CellScore`, `select_qualifier(scores) -> Optional[CellScore]` from `full_python.research.sweep`; `CellScore.rows` keys `"materiality"`, `"expectancy"`, `"trade_count"`, `"drawdown"`, `"outlier_survival"`, `"year_by_year"`, `"side_symmetry"`, `"paired_t"`; `TRADE_CSV_COLUMNS` from `full_python.cli`; `FROZEN_SIMULATION_OVERRIDES` from `scripts.freeze_baseline_anchor`.
- Produces: `build_grid() -> list[dict]`, module constants `GRID_SR_MIN`, `GRID_SR_BUF`, `BASELINE_CELL` (imported by the pinning test).

- [ ] **Step 1: Write the failing test**

Create `tests/test_sweep_sr_driver.py`:

```python
from scripts.sweep_sr_interaction import (
    BASELINE_CELL,
    GRID_SR_BUF,
    GRID_SR_MIN,
    build_grid,
)


def test_sr_grid_is_preregistered_5x4():
    # These literals are locked by the design spec
    # (docs/superpowers/specs/2026-07-05-sr-interaction-sweep-design.md).
    # Changing them is changing the registered experiment -- this test
    # exists to make that impossible to do silently.
    assert GRID_SR_MIN == (10.0, 12.0, 15.0, 18.0, 20.0)
    assert GRID_SR_BUF == (3.0, 5.0, 7.0, 9.0)
    assert BASELINE_CELL == (15.0, 5.0)

    grid = build_grid()
    assert len(grid) == 20
    assert grid.count({}) == 1  # baseline is the empty-override cell
    pairs = {
        (c.get("sr_min_stop_distance", 15.0), c.get("sr_stop_buffer", 5.0))
        for c in grid
    }
    assert len(pairs) == 20
    assert BASELINE_CELL in pairs
    # overrides must be floats (config fields are float; int overrides
    # would alter parameter_hash semantics without changing behavior)
    for cell in grid:
        for value in cell.values():
            assert isinstance(value, float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sweep_sr_driver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.sweep_sr_interaction'`

- [ ] **Step 3: Write the driver**

Create `scripts/sweep_sr_interaction.py`:

```python
#!/usr/bin/env python3
"""Pre-registered Gate 1 Phase 4 sweep: sr_min_stop_distance x sr_stop_buffer.

The last open Phase 4 axis, and the one Phase 0 names explicitly as an
interaction to check jointly (Standard 15). Grid locked by
docs/superpowers/specs/2026-07-05-sr-interaction-sweep-design.md and
pinned by tests/test_sweep_sr_driver.py. Runs the train window only;
NEVER touches holdout. Row 8 (slippage sensitivity) is run separately
for the selected qualifier only, before any holdout decision.

Usage: PYTHONPATH=src:. python3 scripts/sweep_sr_interaction.py
Expected runtime: ~14 minutes (20 cells x ~41s).
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
OUT_DIR = Path("runs/sweeps/sr-grid")
# Truncated bar window validated 2026-07-05 to reproduce the full-history
# train baseline exactly (n=378, net=$65,855).
BARS_START = "2022-11-01T00:00:00Z"
BARS_END = "2025-07-01T00:00:00Z"
TRAIN_START = "2023-01-01T00:00:00Z"
TRAIN_END = "2025-07-01T00:00:00Z"
GRID_SR_MIN = (10.0, 12.0, 15.0, 18.0, 20.0)
GRID_SR_BUF = (3.0, 5.0, 7.0, 9.0)
BASELINE_CELL = (15.0, 5.0)

SCORE_CSV_COLUMNS = [
    "sr_min", "sr_buf", "error", "trade_count", "net_pnl", "delta",
    "materiality_pass", "expectancy_pass", "count_flag", "drawdown_pass",
    "outlier_pass", "years_pass", "sides_pass", "t", "t_pass", "passes_all",
]


def build_grid() -> list[dict]:
    cells = []
    for sr_min in GRID_SR_MIN:
        for sr_buf in GRID_SR_BUF:
            if (sr_min, sr_buf) == BASELINE_CELL:
                cells.append({})
            else:
                cells.append({
                    "sr_min_stop_distance": sr_min,
                    "sr_stop_buffer": sr_buf,
                })
    return cells


def _cell_pair(overrides: dict) -> tuple[float, float]:
    return (
        overrides.get("sr_min_stop_distance", BASELINE_CELL[0]),
        overrides.get("sr_stop_buffer", BASELINE_CELL[1]),
    )


def _cell_name(overrides: dict) -> str:
    sr_min, sr_buf = _cell_pair(overrides)
    return f"srmin_{int(sr_min)}_srbuf_{int(sr_buf)}"


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
        pair = _cell_pair(result.overrides)
        with (cells_dir / f"{_cell_name(result.overrides)}.trades.csv").open(
            "w", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=TRADE_CSV_COLUMNS)
            writer.writeheader()
            for trade in result.trades:
                writer.writerow(trade.to_payload())
        if result.error is None:
            scores[pair] = score_cell(result, baseline)

    qualifier = select_qualifier(list(scores.values()))

    with (OUT_DIR / "scores.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCORE_CSV_COLUMNS)
        writer.writeheader()
        for result in results:
            sr_min, sr_buf = _cell_pair(result.overrides)
            if result.error is not None:
                writer.writerow({"sr_min": sr_min, "sr_buf": sr_buf, "error": result.error})
                continue
            score = scores[(sr_min, sr_buf)]
            rows = score.rows
            writer.writerow({
                "sr_min": sr_min, "sr_buf": sr_buf, "error": "",
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
            "sr_min_stop_distance": list(GRID_SR_MIN),
            "sr_stop_buffer": list(GRID_SR_BUF),
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
            _cell_name(r.overrides): r.error
            for r in results if r.error is not None
        },
        "qualifier": qualifier.overrides if qualifier is not None else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with (OUT_DIR / "summary.json").open("w") as handle:
        json.dump(summary, handle, indent=2)

    print()
    print(f"{'srmin':>6} {'srbuf':>6} {'n':>5} {'net':>10} {'delta':>10} "
          f"{'t':>7}  rows(mat/exp/dd/out/yr/side/t)  ALL")
    for (sr_min, sr_buf), score in sorted(
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
        marker = " BASELINE" if (sr_min, sr_buf) == BASELINE_CELL else ""
        print(f"{int(sr_min):>6} {int(sr_buf):>6} {score.trade_count:>5} "
              f"{score.net_pnl:>10.0f} {score.delta_vs_baseline:>+10.0f} "
              f"{t_text}  {flags:^31}  {'YES' if score.passes_all else 'no'}"
              f"{marker}")
    print()
    if qualifier is None:
        print("NO QUALIFIER -- no cell passed every scored row. Per the "
              "pre-registered rule the S/R interaction axis pair closes on "
              "train evidence (pending the written decision doc).")
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

Run: `python3 -m pytest tests/test_sweep_sr_driver.py -v`
Expected: 1 passed

- [ ] **Step 5: Verify the missing-bars error path**

The worktree has no `runs/multi-year/` data (gitignored), so the driver must exit nonzero with a clear message:

Run: `PYTHONPATH=src:. python3 scripts/sweep_sr_interaction.py; echo "exit=$?"`
Expected output: `ERROR: bars file not found: runs/multi-year/nq1_2021-03-16_2026-06-26.csv` and `exit=1`

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: 155 passed, 2 skipped

- [ ] **Step 7: Commit**

```bash
git add scripts/sweep_sr_interaction.py tests/test_sweep_sr_driver.py
git commit -m "feat: pre-registered sr_min x sr_buffer interaction sweep driver"
```

---

## Not in this plan (deliberate)

- **Running the actual 20-cell sweep** — happens after merge, from the main clone where the bars CSV exists.
- Row 8 slippage runs, the evaluation/closing decision doc, and the holdout step.
- Any refactor of `scripts/sweep_ma_lengths.py` (retired, kept as audit artifact).
