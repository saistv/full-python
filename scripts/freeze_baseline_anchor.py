"""Freeze the Python Baseline Anchor: one reproducible command.

Replaces the missing 3-year TV export as the reference point (see
docs/decisions/2026-07-04-python-baseline-anchor.md). Data span is the
2025-10-01 -> 2026-06-26 window that is actually reconciled against
TradingView (106/106 AM/DLL trades matched) -- NOT a 3-year window, which
does not exist in this repo. Run with:

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
    # SimulationConfig defaults this to 1.0, stacking on top of
    # entry_slippage_points during the 9:30-9:45 ET window. Adaptive
    # Trend's entry window starts at 9:30, so almost every trade fires
    # there -- leaving this at its default silently doubles slippage on
    # nearly every entry and breaks TV parity (confirmed empirically: TV
    # trade #1's fill is bar-open + exactly 3 ticks / 0.75, not 1.75).
    "rth_open_extra_entry_slippage_points": 0.0,
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
