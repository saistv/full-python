from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
import io
from pathlib import Path
from typing import Any

import zstandard

from full_python.data.databento import REQUIRED_OHLCV_COLUMNS


@dataclass(frozen=True)
class SymbolInventory:
    row_count: int
    start_timestamp_utc: str
    end_timestamp_utc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DatabentoFileInventory:
    path: str
    file_size_bytes: int
    symbols: dict[str, SymbolInventory]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "file_size_bytes": self.file_size_bytes,
            "symbols": {
                symbol: inventory.to_dict()
                for symbol, inventory in sorted(self.symbols.items())
            },
        }


@dataclass
class _MutableSymbolInventory:
    row_count: int
    start_timestamp_utc: str
    end_timestamp_utc: str

    def record(self, timestamp_utc: str) -> None:
        self.row_count += 1
        if timestamp_utc < self.start_timestamp_utc:
            self.start_timestamp_utc = timestamp_utc
        if timestamp_utc > self.end_timestamp_utc:
            self.end_timestamp_utc = timestamp_utc

    def freeze(self) -> SymbolInventory:
        return SymbolInventory(
            row_count=self.row_count,
            start_timestamp_utc=self.start_timestamp_utc,
            end_timestamp_utc=self.end_timestamp_utc,
        )


def inspect_databento_ohlcv_file(
    path: str | Path,
    symbol_root: str = "NQ",
) -> DatabentoFileInventory:
    input_path = Path(path)
    symbols: dict[str, _MutableSymbolInventory] = {}
    decompressor = zstandard.ZstdDecompressor()
    with input_path.open("rb") as compressed:
        with decompressor.stream_reader(compressed) as stream:
            text_stream = io.TextIOWrapper(stream, encoding="utf-8", newline="")
            reader = csv.DictReader(text_stream)
            _validate_columns(reader.fieldnames)
            for row in reader:
                symbol = row["symbol"]
                if not symbol.startswith(symbol_root):
                    continue
                timestamp_utc = _normalize_timestamp(row["ts_event"])
                if symbol in symbols:
                    symbols[symbol].record(timestamp_utc)
                else:
                    symbols[symbol] = _MutableSymbolInventory(
                        row_count=1,
                        start_timestamp_utc=timestamp_utc,
                        end_timestamp_utc=timestamp_utc,
                    )

    return DatabentoFileInventory(
        path=str(input_path),
        file_size_bytes=input_path.stat().st_size,
        symbols={
            symbol: inventory.freeze()
            for symbol, inventory in sorted(symbols.items())
        },
    )


def inspect_databento_ohlcv_folder(
    folder: str | Path,
    symbol_root: str = "NQ",
) -> list[DatabentoFileInventory]:
    folder_path = Path(folder)
    return [
        inspect_databento_ohlcv_file(path, symbol_root=symbol_root)
        for path in sorted(folder_path.glob("*.ohlcv-1m.csv.zst"))
    ]


def _validate_columns(fieldnames: list[str] | None) -> None:
    available = set(fieldnames or [])
    missing = sorted(REQUIRED_OHLCV_COLUMNS - available)
    if missing:
        raise ValueError(f"Missing required Databento OHLCV columns: {', '.join(missing)}")


def _normalize_timestamp(timestamp: str) -> str:
    if timestamp.endswith(".000000000Z"):
        return f"{timestamp[:-11]}Z"
    return timestamp
