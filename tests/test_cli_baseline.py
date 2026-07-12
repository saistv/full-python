import json
import os
from pathlib import Path
import subprocess
import sys

from full_python.cli import run_baseline


def test_run_baseline_writes_event_log_and_report(tmp_path: Path) -> None:
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

    events_path = output_dir / "events.jsonl"
    assert events_path.exists()
    event_lines = events_path.read_text(encoding="utf-8").splitlines()
    assert event_lines
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["strategy"]["name"] == "baseline_momentum_breakout"
    assert report["data"]["path"] == str(data_path)
    assert len(report["data"]["content_sha256"]) == 64
    assert report["data"]["row_count"] == 3
    assert report["data"]["file_size_bytes"] == data_path.stat().st_size
    assert report["data"]["column_map"]["timestamp"] == "timestamp"
    assert report["survivability"]["trade_count"] == 0
    assert len(report["strategy"]["parameter_hash"]) == 64


def test_adaptive_strategy_uses_execution_instrument_point_value(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:30:00Z,NQU2026,100,101,99,100,10\n"
        "2026-06-30T13:31:00Z,NQU2026,100,102,99,101,10\n",
        encoding="utf-8",
    )

    report_path = run_baseline(
        data_path=data_path,
        output_dir=tmp_path / "mnq-run",
        strategy_name="adaptive_trend_am",
        execution_instrument="MNQ",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["execution_instrument"]["root"] == "MNQ"
    assert report["simulation"]["point_value"] == 2.0
    assert report["strategy"]["dollar_point_value"] == 2.0


def test_run_baseline_manifest_hash_changes_when_csv_contents_change(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:30:00Z,NQU2026,100,101,99,100,10\n"
        "2026-06-30T13:31:00Z,NQU2026,100,102,99,101,10\n"
        "2026-06-30T13:32:00Z,NQU2026,101,103,100,102.5,10\n",
        encoding="utf-8",
    )

    first_report_path = run_baseline(data_path=data_path, output_dir=tmp_path / "first-run")
    first_report = json.loads(first_report_path.read_text(encoding="utf-8"))

    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:30:00Z,NQU2026,100,101,99,100,10\n"
        "2026-06-30T13:31:00Z,NQU2026,100,105,99,104,10\n"
        "2026-06-30T13:32:00Z,NQU2026,101,103,100,102.5,10\n",
        encoding="utf-8",
    )

    second_report_path = run_baseline(data_path=data_path, output_dir=tmp_path / "second-run")
    second_report = json.loads(second_report_path.read_text(encoding="utf-8"))

    assert first_report["data"]["content_sha256"] != second_report["data"]["content_sha256"]
    assert first_report["data"]["manifest_hash"] != second_report["data"]["manifest_hash"]


def test_cli_module_entrypoint_writes_outputs(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:30:00Z,NQU2026,100,101,99,100,10\n"
        "2026-06-30T13:31:00Z,NQU2026,100,102,99,101,10\n"
        "2026-06-30T13:32:00Z,NQU2026,101,103,100,102.5,10\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "cli-run"
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "full_python.cli",
            "--data",
            str(data_path),
            "--output-dir",
            str(output_dir),
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert str(output_dir / "report.json") in completed.stdout
    assert (output_dir / "events.jsonl").exists()
    assert (output_dir / "report.json").exists()


def test_readme_documents_checkout_module_command() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "PYTHONPATH=src python3 -m full_python.cli --data path/to/bars.csv --output-dir runs/baseline-smoke" in readme
