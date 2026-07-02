import csv
import json
import os
from pathlib import Path
import subprocess
import sys


def test_cli_sweep_exit_branch_writes_ranked_outputs(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:30:00Z,NQU2026,100,101,99,100,10\n"
        "2026-06-30T13:31:00Z,NQU2026,100,102,99,101,10\n"
        "2026-06-30T13:32:00Z,NQU2026,101,131,100,130,10\n"
        "2026-06-30T13:33:00Z,NQU2026,130,175,129,170,10\n"
        "2026-06-30T13:34:00Z,NQU2026,170,172,153,155,10\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "sweep"
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "full_python.cli",
            "sweep-exit-branch",
            "--data",
            str(data_path),
            "--output-dir",
            str(output_dir),
            "--stream-input",
            "--point-value",
            "2",
            "--slippage-points-per-side",
            "0",
            "--commission-per-contract",
            "0",
            "--mfe-activations",
            "40,80",
            "--mfe-givebacks",
            "20",
            "--fresh-breakout-clearances",
            "0",
            "--cooldowns",
            "0",
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    json_path = output_dir / "sweep_results.json"
    csv_path = output_dir / "sweep_results.csv"
    assert str(json_path) in completed.stdout
    assert json_path.exists()
    assert csv_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["combo_count"] == 2
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["mfe_trailing_activation_points"] == "40.0"
