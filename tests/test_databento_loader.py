from pathlib import Path

import pytest
import zstandard

from full_python.data.databento import load_databento_ohlcv_bars


def write_zst_csv(path: Path, content: str) -> None:
    compressor = zstandard.ZstdCompressor()
    path.write_bytes(compressor.compress(content.encode("utf-8")))


def test_load_databento_ohlcv_bars_filters_nq_outrights_and_sorts(tmp_path: Path) -> None:
    data_path = tmp_path / "tiny.ohlcv-1m.csv.zst"
    write_zst_csv(
        data_path,
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
        "2026-06-30T13:31:00.000000000Z,33,1,2,101,103,100,102,20,NQU2026\n"
        "2026-06-30T13:30:00.000000000Z,33,1,3,200,201,199,200.5,30,ESU2026\n"
        "2026-06-30T13:30:00.000000000Z,33,1,4,10,11,9,10.5,40,NQU2026-NQZ2026\n"
        "2026-06-30T13:30:00.000000000Z,33,1,1,100,101,99,100.5,10,NQU2026\n",
    )

    bars = load_databento_ohlcv_bars(data_path)

    assert [bar.symbol for bar in bars] == ["NQU2026", "NQU2026"]
    assert [bar.timestamp_utc for bar in bars] == [
        "2026-06-30T13:30:00Z",
        "2026-06-30T13:31:00Z",
    ]
    assert bars[0].open == 100.0
    assert bars[0].high == 101.0
    assert bars[0].low == 99.0
    assert bars[0].close == 100.5
    assert bars[0].volume == 10.0


def test_load_databento_ohlcv_bars_can_include_matching_spreads(tmp_path: Path) -> None:
    data_path = tmp_path / "tiny.ohlcv-1m.csv.zst"
    write_zst_csv(
        data_path,
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
        "2026-06-30T13:30:00.000000000Z,33,1,1,100,101,99,100.5,10,NQU2026\n"
        "2026-06-30T13:30:00.000000000Z,33,1,2,10,11,9,10.5,40,NQU2026-NQZ2026\n",
    )

    bars = load_databento_ohlcv_bars(data_path, include_spreads=True)

    assert [bar.symbol for bar in bars] == ["NQU2026", "NQU2026-NQZ2026"]


def test_load_databento_ohlcv_bars_names_missing_required_columns(tmp_path: Path) -> None:
    data_path = tmp_path / "missing.ohlcv-1m.csv.zst"
    write_zst_csv(
        data_path,
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,symbol\n"
        "2026-06-30T13:30:00.000000000Z,33,1,1,100,101,99,100.5,NQU2026\n",
    )

    with pytest.raises(ValueError, match="volume"):
        load_databento_ohlcv_bars(data_path)
