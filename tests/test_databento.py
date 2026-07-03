import csv
from datetime import date
from pathlib import Path

import pytest

zstandard = pytest.importorskip("zstandard")

from full_python.data.databento import (
    convert_glbx_files,
    front_contract_for_session,
    third_friday,
)


def test_third_friday_examples() -> None:
    assert third_friday(2025, 12) == date(2025, 12, 19)
    assert third_friday(2026, 3) == date(2026, 3, 20)
    assert third_friday(2026, 6) == date(2026, 6, 19)


def test_front_contract_rolls_tuesday_of_expiration_week() -> None:
    # NQZ5 expires Fri Dec 19 2025 -> roll boundary Dec 16 (Tuesday).
    assert front_contract_for_session(date(2025, 12, 15)) == "NQZ5"
    assert front_contract_for_session(date(2025, 12, 16)) == "NQH6"
    # NQM6 expires Jun 19 2026 -> NQU6 from Jun 16.
    assert front_contract_for_session(date(2026, 6, 15)) == "NQM6"
    assert front_contract_for_session(date(2026, 6, 16)) == "NQU6"
    assert front_contract_for_session(date(2025, 10, 28)) == "NQZ5"


GLBX_HEADER = "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"


def _write_zst(path: Path, rows: str) -> Path:
    path.write_bytes(zstandard.ZstdCompressor().compress((GLBX_HEADER + rows).encode()))
    return path


def test_convert_keeps_front_month_only_and_drops_spreads(tmp_path: Path) -> None:
    data = (
        "2025-10-28T14:30:00.000000000Z,33,1,1,25000.0,25010.0,24990.0,25005.0,100,NQZ5\n"
        "2025-10-28T14:30:00.000000000Z,33,1,2,25100.0,25110.0,25090.0,25105.0,50,NQH6\n"
        "2025-10-28T14:30:00.000000000Z,33,1,3,-90.0,-90.0,-90.0,-90.0,5,NQZ5-NQH6\n"
        "2025-10-28T14:31:00.000000000Z,33,1,1,25005.0,25015.0,25000.0,25010.0,80,NQZ5\n"
    )
    input_path = _write_zst(tmp_path / "glbx-mdp3-20251028.ohlcv-1m.csv.zst", data)
    output_path = tmp_path / "out.csv"

    summary = convert_glbx_files([input_path], output_path)

    assert summary["rows_written"] == 2
    assert summary["other_contract_rows_skipped"] == 1  # NQH6 (spread excluded earlier)
    with output_path.open() as handle:
        rows = list(csv.DictReader(handle))
    assert [row["close"] for row in rows] == ["25005.0", "25010.0"]
    assert rows[0]["symbol"] == "NQ1!"
    assert rows[0]["timestamp"] == "2025-10-28T14:30:00Z"
    assert summary["roll_map"] == {"2025-10-28": "NQZ5"}


def test_convert_switches_contract_across_roll_boundary(tmp_path: Path) -> None:
    data = (
        # Dec 15 session -> NQZ5 is front; Dec 16 session -> NQH6 is front.
        "2025-12-15T14:30:00.000000000Z,33,1,1,25000.0,25000.0,25000.0,25000.0,10,NQZ5\n"
        "2025-12-15T14:30:00.000000000Z,33,1,2,25090.0,25090.0,25090.0,25090.0,10,NQH6\n"
        "2025-12-16T14:30:00.000000000Z,33,1,1,25010.0,25010.0,25010.0,25010.0,10,NQZ5\n"
        "2025-12-16T14:30:00.000000000Z,33,1,2,25100.0,25100.0,25100.0,25100.0,10,NQH6\n"
    )
    input_path = _write_zst(tmp_path / "glbx-mdp3-20251215.ohlcv-1m.csv.zst", data)
    output_path = tmp_path / "out.csv"

    summary = convert_glbx_files([input_path], output_path)

    with output_path.open() as handle:
        closes = [row["close"] for row in csv.DictReader(handle)]
    assert closes == ["25000.0", "25100.0"]  # NQZ5 on the 15th, NQH6 on the 16th
    assert summary["roll_map"] == {"2025-12-15": "NQZ5", "2025-12-16": "NQH6"}


def test_evening_bars_belong_to_next_session_for_roll_purposes(tmp_path: Path) -> None:
    # Dec 15 23:30 UTC = Dec 15 18:30 ET -> session Dec 16 -> NQH6 already front.
    data = (
        "2025-12-15T23:30:00.000000000Z,33,1,1,25010.0,25010.0,25010.0,25010.0,10,NQZ5\n"
        "2025-12-15T23:30:00.000000000Z,33,1,2,25100.0,25100.0,25100.0,25100.0,10,NQH6\n"
    )
    input_path = _write_zst(tmp_path / "glbx-mdp3-20251215.ohlcv-1m.csv.zst", data)
    output_path = tmp_path / "out.csv"

    convert_glbx_files([input_path], output_path)

    with output_path.open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["close"] == "25100.0"
