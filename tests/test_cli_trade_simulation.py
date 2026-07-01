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


def test_cli_simulate_baseline_trades_accepts_rth_and_cost_options(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:29:00Z,NQU2026,100,101,99,100,10\n"
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

    subprocess.run(
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
            "--session",
            "rth",
            "--point-value",
            "2",
            "--slippage-points-per-side",
            "1",
            "--commission-per-contract",
            "1",
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    summary = json.loads((output_dir / "trade_summary.json").read_text(encoding="utf-8"))
    assert summary["assumptions"]["session"] == "rth"
    assert summary["assumptions"]["point_value"] == 2.0
    assert summary["assumptions"]["slippage_points_per_side"] == 1.0
    assert summary["assumptions"]["commission_per_contract"] == 1.0
    assert summary["trade_count"] == 1


def test_cli_simulate_baseline_trades_accepts_symbol_change_exit_mode(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:30:00Z,NQM2026,100,130,99,125,10\n"
        "2026-06-30T13:31:00Z,NQM2026,125,132,124,131,10\n"
        "2026-06-30T13:32:00Z,NQU2026,150,152,149,151,10\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "trade-run"
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")

    subprocess.run(
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
            "--symbol-change-exit-mode",
            "previous_close",
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    summary = json.loads((output_dir / "trade_summary.json").read_text(encoding="utf-8"))
    assert summary["assumptions"]["symbol_change_exit_mode"] == "previous_close"
    with (output_dir / "trades.csv").open(encoding="utf-8", newline="") as handle:
        trades = list(csv.DictReader(handle))
    assert "max_favorable_excursion_points" in trades[0]
    assert "max_adverse_excursion_points" in trades[0]
