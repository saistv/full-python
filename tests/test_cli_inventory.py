import json
import os
from pathlib import Path
import subprocess
import sys

import zstandard


def write_zst_csv(path: Path, content: str) -> None:
    compressor = zstandard.ZstdCompressor()
    path.write_bytes(compressor.compress(content.encode("utf-8")))


def test_cli_inventory_databento_writes_json_and_markdown(tmp_path: Path) -> None:
    write_zst_csv(
        tmp_path / "a.ohlcv-1m.csv.zst",
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
        "2025-02-03T00:00:00.000000000Z,33,1,1,100,101,99,100.5,10,NQH5\n"
        "2025-02-03T00:01:00.000000000Z,33,1,1,101,102,100,101.5,11,NQH5\n"
        "2025-02-03T00:00:00.000000000Z,33,1,2,200,201,199,200.5,20,NQM5\n",
    )
    output_dir = tmp_path / "inventory-run"
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "full_python.cli",
            "inventory-databento",
            "--folder",
            str(tmp_path),
            "--output-dir",
            str(output_dir),
            "--markdown",
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    json_path = output_dir / "contract_inventory.json"
    markdown_path = output_dir / "contract_inventory.md"
    assert str(json_path) in completed.stdout
    assert json_path.exists()
    assert markdown_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["source_format"] == "databento-ohlcv"
    assert payload["symbol_root"] == "NQ"
    assert payload["files"][0]["symbols"]["NQH5"]["row_count"] == 2
    assert "NQH5" in markdown_path.read_text(encoding="utf-8")
