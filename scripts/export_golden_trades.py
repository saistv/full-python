# scripts/export_golden_trades.py
"""Serialize the frozen anchor's trade ledger into tests/fixtures/golden_trades.json.

Run once, by hand, after Task 4's freeze produces runs/baseline-anchor/trades.csv:

    PYTHONPATH=src python3 scripts/export_golden_trades.py
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

TRADES_CSV = Path(os.environ.get(
    "FULL_PYTHON_GOLDEN_TRADES",
    "runs/verify-anchor/trades.csv",
))
FIXTURE_PATH = Path("tests/fixtures/golden_trades.json")


def export_golden_trades() -> None:
    with TRADES_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    export_golden_trades()
