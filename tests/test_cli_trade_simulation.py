import csv
import json
import os
from pathlib import Path
import subprocess
import sys


def test_cli_simulate_baseline_trades_writes_trade_outputs(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:30:00Z,NQU2026,100,101,99,100,10\n"
        "2026-06-30T13:31:00Z,NQU2026,100,130,99,125,10\n"
        "2026-06-30T13:32:00Z,NQU2026,125,132,124,131,10\n"
        "2026-06-30T13:33:00Z,NQU2026,131,132,100,101,10\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "trade-run"
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "full_python.cli",
            "simulate-baseline-trades",
            "--data",
            str(data_path),
            "--output-dir",
            str(output_dir),
            "--stream-input",
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    trades_path = output_dir / "trades.csv"
    summary_path = output_dir / "trade_summary.json"
    assert str(trades_path) in completed.stdout
    assert trades_path.exists()
    assert summary_path.exists()
    with trades_path.open(encoding="utf-8", newline="") as handle:
        trades = list(csv.DictReader(handle))
    assert trades
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["trade_count"] == len(trades)
    assert summary["assumptions"]["entry_fill"] == "current_bar_close"
