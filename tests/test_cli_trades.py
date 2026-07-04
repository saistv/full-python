import csv
import json
from pathlib import Path

import pytest

from full_python.cli import run_baseline

TRADE_PRODUCING_CSV = (
    "timestamp,symbol,open,high,low,close,volume\n"
    "2026-06-30T13:30:00Z,NQU2026,100,101,99,100,10\n"
    "2026-06-30T13:31:00Z,NQU2026,100,102,99,101,10\n"
    "2026-06-30T13:32:00Z,NQU2026,101,103,100,102.5,10\n"
    "2026-06-30T13:33:00Z,NQU2026,103,104,102,103.5,10\n"
    "2026-06-30T13:34:00Z,NQU2026,104,105,60,62,10\n"
)


def test_cli_produces_real_trades_and_reports(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(TRADE_PRODUCING_CSV, encoding="utf-8")

    report_path = run_baseline(data_path=data_path, output_dir=tmp_path / "run")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["survivability"]["trade_count"] == 1
    assert report["survivability"]["net_pnl"] != 0.0
    assert report["quality"]["is_structurally_clean"]
    assert report["daily"]["trading_days"] == 1
    assert report["exit_reasons"] == {"stop": 1}
    assert len(report["run_id"]) == 35  # four 8-char hashes joined by dashes

    with (tmp_path / "run" / "trades.csv").open() as handle:
        trades = list(csv.DictReader(handle))
    assert len(trades) == 1
    trade = trades[0]
    # Breakout signal at 13:32 close=102.5 -> fill next bar open 103 + 2.0
    # slippage (1.0 base + 1.0 RTH open window) = 105; stop 72.5 hit on the
    # 13:34 bar -> exit 72.0 after slippage.
    assert trade["entry_timestamp_utc"] == "2026-06-30T13:33:00Z"
    assert float(trade["entry_price"]) == 105.0
    assert trade["exit_reason"] == "stop"
    assert float(trade["exit_price"]) == 72.0
    assert float(trade["stop_price"]) == 72.5

    daily_lines = (tmp_path / "run" / "daily_pnl.csv").read_text().splitlines()
    assert daily_lines[0] == "session_date,net_pnl,cumulative_pnl"
    assert daily_lines[1].startswith("2026-06-30,")


def test_cli_run_ids_and_events_are_deterministic(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(TRADE_PRODUCING_CSV, encoding="utf-8")

    first = json.loads(
        run_baseline(data_path=data_path, output_dir=tmp_path / "a").read_text()
    )
    second = json.loads(
        run_baseline(data_path=data_path, output_dir=tmp_path / "b").read_text()
    )

    assert first["run_id"] == second["run_id"]
    assert (tmp_path / "a" / "events.jsonl").read_bytes() == (
        tmp_path / "b" / "events.jsonl"
    ).read_bytes()


def test_cli_refuses_structurally_dirty_data_unless_allowed(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:31:00Z,NQU2026,100,101,99,100,10\n"
        "2026-06-30T13:30:00Z,NQU2026,100,102,99,101,10\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="structural validation"):
        run_baseline(data_path=data_path, output_dir=tmp_path / "run")

    report_path = run_baseline(
        data_path=data_path, output_dir=tmp_path / "run2", allow_dirty_data=True
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert not report["quality"]["is_structurally_clean"]


def test_signal_bar_close_mode_changes_fills(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(TRADE_PRODUCING_CSV, encoding="utf-8")

    report_path = run_baseline(
        data_path=data_path,
        output_dir=tmp_path / "run",
        fill_timing="signal_bar_close",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["simulation"]["fill_timing"] == "signal_bar_close"
    with (tmp_path / "run" / "trades.csv").open() as handle:
        trade = list(csv.DictReader(handle))[0]
    assert trade["entry_timestamp_utc"] == "2026-06-30T13:32:00Z"


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
    assert len(report["code_version"]) == 40


def test_code_version_fallback_uses_null_sha(tmp_path: Path, monkeypatch) -> None:
    """Test that _code_version_hash() fallback returns a 40-char string for consistent run_id segments."""
    from full_python import cli

    def mock_subprocess_run(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr("full_python.cli.subprocess.run", mock_subprocess_run)
    code_version = cli._code_version_hash()

    assert code_version == "0" * 40
    assert len(code_version) == 40
    assert code_version[:8] == "00000000"
