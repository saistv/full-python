from pathlib import Path

from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.manifest import DataManifest, file_sha256


def test_data_manifest_has_stable_hash() -> None:
    manifest = DataManifest(
        dataset_name="tiny-nq",
        source="fixture",
        symbol="NQ",
        contract="NQU2026",
        timezone="UTC",
        session="RTH",
        start_timestamp_utc="2026-06-30T13:30:00Z",
        end_timestamp_utc="2026-06-30T13:31:00Z",
        path="tests/fixtures/tiny_nq.csv",
        content_sha256="a" * 64,
        row_count=2,
        file_size_bytes=123,
        column_map={
            "timestamp": "timestamp",
            "symbol": "symbol",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        },
    )

    assert manifest.stable_hash() == manifest.stable_hash()
    assert len(manifest.stable_hash()) == 64
    manifest_dict = manifest.to_dict()
    assert manifest_dict["contract"] == "NQU2026"
    assert manifest_dict["content_sha256"] == "a" * 64
    assert manifest_dict["row_count"] == 2
    assert manifest_dict["file_size_bytes"] == 123
    assert manifest_dict["column_map"]["timestamp"] == "timestamp"


def test_file_sha256_hashes_file_contents(tmp_path: Path) -> None:
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text("timestamp,symbol\n2026-06-30T13:30:00Z,NQU2026\n", encoding="utf-8")

    first_hash = file_sha256(csv_path)

    csv_path.write_text("timestamp,symbol\n2026-06-30T13:30:00Z,MNQU2026\n", encoding="utf-8")
    assert len(first_hash) == 64
    assert first_hash != file_sha256(csv_path)


def test_load_csv_bars_converts_rows_to_market_bars(tmp_path: Path) -> None:
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "ts,symbol,o,h,l,c,v\n"
        "2026-06-30T13:30:00Z,NQU2026,100,101,99,100.5,10\n"
        "2026-06-30T13:31:00Z,NQU2026,100.5,102,100.25,101.75,12\n",
        encoding="utf-8",
    )
    column_map = CsvBarColumnMap(
        timestamp="ts",
        symbol="symbol",
        open="o",
        high="h",
        low="l",
        close="c",
        volume="v",
    )

    bars = load_csv_bars(csv_path, column_map)

    assert len(bars) == 2
    assert bars[0].timestamp_utc == "2026-06-30T13:30:00Z"
    assert bars[0].symbol == "NQU2026"
    assert bars[1].close == 101.75
    assert bars[1].volume == 12.0
