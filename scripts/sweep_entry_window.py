#!/usr/bin/env python3
"""Pre-registered Gate 1 sweep: the entry window (entry_start_minutes_et x
entry_end_minutes_et).

The trading window (default 9:30-10:00 ET) is a FIXED ASSUMPTION Gate 1
never swept -- the TV era found the window is the core filter and rejected
window EXPANSION, but never tested different/later windows under the
Python engine + AM/DLL sizing. This sweep re-verifies with the same
discipline. Grid locked below and pinned by tests/test_sweep_window_driver.py.
Runs the train window only; NEVER touches holdout. Row 8 (slippage) is run
separately for a selected qualifier only, before any holdout decision.

Usage: PYTHONPATH=src:. python3 scripts/sweep_entry_window.py
Expected runtime: ~6 minutes (8 cells x ~41s).
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
OUT_DIR = Path("runs/sweeps/window-grid")
# Truncated bar window validated 2026-07-05 to reproduce the full-history
# train baseline exactly (n=378, net=$65,855).
BARS_START = "2022-11-01T00:00:00Z"
BARS_END = "2025-07-01T00:00:00Z"
TRAIN_START = "2023-01-01T00:00:00Z"
TRAIN_END = "2025-07-01T00:00:00Z"
# Minutes-from-midnight ET. Starts 9:30 / 9:45 / 10:00 / 10:30; durations 30/60.
GRID_STARTS = (570, 585, 600, 630)
GRID_DURATIONS = (30, 60)
BASELINE_CELL = (570, 600)  # 9:30-10:00, the current production window

SCORE_CSV_COLUMNS = [
    "start", "end", "error", "trade_count", "net_pnl", "delta",
    "materiality_pass", "expectancy_pass", "count_flag", "drawdown_pass",
    "outlier_pass", "years_pass", "sides_pass", "t", "t_pass", "passes_all",
]


def build_grid() -> list[dict]:
    cells = []
    for start in GRID_STARTS:
        for duration in GRID_DURATIONS:
            end = start + duration
            if (start, end) == BASELINE_CELL:
                cells.append({})
            else:
                cells.append({
                    "entry_start_minutes_et": start,
                    "entry_end_minutes_et": end,
                })
    return cells


def _cell_pair(overrides: dict) -> tuple[int, int]:
    return (
        overrides.get("entry_start_minutes_et", BASELINE_CELL[0]),
        overrides.get("entry_end_minutes_et", BASELINE_CELL[1]),
    )


def _cell_name(overrides: dict) -> str:
    start, end = _cell_pair(overrides)
    return f"win_{start}_{end}"


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
            start, end = _cell_pair(result.overrides)
            if result.error is not None:
                writer.writerow({"start": start, "end": end, "error": result.error})
                continue
            score = scores[(start, end)]
            rows = score.rows
            writer.writerow({
                "start": start, "end": end, "error": "",
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
            "starts_minutes_et": list(GRID_STARTS),
            "durations_minutes": list(GRID_DURATIONS),
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
    print(f"{'start':>6} {'end':>5} {'n':>5} {'net':>10} {'delta':>10} "
          f"{'t':>7}  rows(mat/exp/dd/out/yr/side/t)  ALL")
    for (start, end), score in sorted(
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
        marker = " BASELINE" if (start, end) == BASELINE_CELL else ""
        print(f"{start:>6} {end:>5} {score.trade_count:>5} "
              f"{score.net_pnl:>10.0f} {score.delta_vs_baseline:>+10.0f} "
              f"{t_text}  {flags:^31}  {'YES' if score.passes_all else 'no'}"
              f"{marker}")
    print()
    if qualifier is None:
        print("NO QUALIFIER -- no cell passed every scored row. Per the "
              "pre-registered rule the entry-window axis closes on train "
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
