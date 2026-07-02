from pathlib import Path

import zstandard

from full_python.data.inventory import (
    inspect_databento_ohlcv_file,
    inspect_databento_ohlcv_folder,
)


def write_zst_csv(path: Path, content: str) -> None:
    compressor = zstandard.ZstdCompressor()
    path.write_bytes(compressor.compress(content.encode("utf-8")))


def test_inventory_counts_symbols_and_timestamps(tmp_path: Path) -> None:
    data_path = tmp_path / "one.ohlcv-1m.csv.zst"
    write_zst_csv(
        data_path,
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
        "2025-02-03T00:00:00.000000000Z,33,1,1,100,101,99,100.5,10,NQH5\n"
        "2025-02-03T00:01:00.000000000Z,33,1,1,101,102,100,101.5,11,NQH5\n"
        "2025-02-03T00:00:00.000000000Z,33,1,2,200,201,199,200.5,20,NQM5\n"
        "2025-02-03T00:00:00.000000000Z,33,1,3,10,11,9,10.5,30,ESH5\n",
    )

    inventory = inspect_databento_ohlcv_file(data_path)

    assert inventory.path == str(data_path)
    assert inventory.file_size_bytes == data_path.stat().st_size
    assert sorted(inventory.symbols) == ["NQH5", "NQM5"]
    assert inventory.symbols["NQH5"].row_count == 2
    assert inventory.symbols["NQH5"].start_timestamp_utc == "2025-02-03T00:00:00Z"
    assert inventory.symbols["NQH5"].end_timestamp_utc == "2025-02-03T00:01:00Z"
    assert inventory.symbols["NQM5"].row_count == 1


def test_inventory_folder_sorts_files(tmp_path: Path) -> None:
    write_zst_csv(
        tmp_path / "b.ohlcv-1m.csv.zst",
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
        "2025-02-04T00:00:00.000000000Z,33,1,1,100,101,99,100.5,10,NQH5\n",
    )
    write_zst_csv(
        tmp_path / "a.ohlcv-1m.csv.zst",
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
        "2025-02-03T00:00:00.000000000Z,33,1,1,100,101,99,100.5,10,NQH5\n",
    )
    write_zst_csv(
        tmp_path / "ignored.bbo-1m.csv.zst",
        "ts_event,rtype,publisher_id,instrument_id,bid_px_00,ask_px_00,symbol\n"
        "2025-02-03T00:00:00.000000000Z,33,1,1,100,101,NQH5\n",
    )

    inventories = inspect_databento_ohlcv_folder(tmp_path)

    assert [Path(item.path).name for item in inventories] == [
        "a.ohlcv-1m.csv.zst",
        "b.ohlcv-1m.csv.zst",
    ]
