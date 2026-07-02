import csv
import json
import os
from pathlib import Path
import subprocess
import sys

import zstandard


def write_zst_csv(path: Path, content: str) -> None:
    compressor = zstandard.ZstdCompressor()
    path.write_bytes(compressor.compress(content.encode("utf-8")))


def test_cli_build_selected_stream_writes_csv_and_manifest(tmp_path: Path) -> None:
    write_zst_csv(
        tmp_path / "glbx-mdp3-20250203.ohlcv-1m.csv.zst",
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
        "2025-02-03T00:00:00.000000000Z,33,1,1,100,101,99,100.5,10,NQH5\n"
        "2025-02-03T00:01:00.000000000Z,33,1,1,101,102,100,101.5,11,NQH5\n"
        "2025-02-03T00:00:00.000000000Z,33,1,2,200,201,199,200.5,20,NQM5\n",
    )
    output_dir = tmp_path / "selected-stream-run"
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "full_python.cli",
            "build-selected-stream",
            "--folder",
            str(tmp_path),
            "--output-dir",
            str(output_dir),
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    csv_path = output_dir / "selected_bars.csv"
    manifest_path = output_dir / "selected_bars_manifest.json"
    assert str(csv_path) in completed.stdout
    assert csv_path.exists()
    assert manifest_path.exists()
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["symbol"] == "NQH5"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["row_count"] == 2
    assert manifest["selection_rule"] == "dominant_outright_row_count"
