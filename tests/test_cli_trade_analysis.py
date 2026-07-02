import json
import os
from pathlib import Path
import subprocess
import sys


def test_cli_analyze_trades_writes_trade_analysis(tmp_path: Path) -> None:
    trades_path = tmp_path / "trades.csv"
    trades_path.write_text(
        "trade_id,symbol,side,quantity,entry_timestamp_utc,entry_price,exit_timestamp_utc,"
        "exit_price,exit_reason,stop_price,pnl_points,gross_pnl_dollars,"
        "commission_dollars,net_pnl_dollars\n"
        "1,NQH6,long,1,2026-01-05T14:30:00Z,100,2026-01-05T14:40:00Z,"
        "95,stop,95,-5,-10,2,-12\n"
        "2,NQH6,long,1,2026-01-06T14:30:00Z,100,2026-01-06T14:40:00Z,"
        "120,symbol_change,95,20,40,2,38\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "analysis"
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "full_python.cli",
            "analyze-trades",
            "--trades",
            str(trades_path),
            "--output-dir",
            str(output_dir),
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    report_path = output_dir / "trade_analysis.json"
    assert str(report_path) in completed.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["trade_count"] == 2
    assert report["summary"]["total_net_pnl_dollars"] == 26.0
    assert report["risk"]["max_drawdown_dollars"] == -12.0
