import json
from pathlib import Path

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
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["strategy"]["name"] == "baseline_momentum_breakout"
    assert report["data"]["path"] == str(data_path)
    assert report["survivability"]["trade_count"] == 0
    assert len(report["strategy"]["parameter_hash"]) == 64
