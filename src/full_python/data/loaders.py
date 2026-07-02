from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Iterator
from pathlib import Path

from full_python.models import MarketBar


@dataclass(frozen=True)
class CsvBarColumnMap:
    timestamp: str
    symbol: str
    open: str
    high: str
    low: str
    close: str
    volume: str


@dataclass(frozen=True)
class CsvBarProfile:
    row_count: int
    start_timestamp_utc: str
    end_timestamp_utc: str
    symbols: tuple[str, ...]


def load_csv_bars(path: str | Path, column_map: CsvBarColumnMap) -> list[MarketBar]:
    return list(iter_csv_bars(path, column_map))


def iter_csv_bars(path: str | Path, column_map: CsvBarColumnMap) -> Iterator[MarketBar]:
    input_path = Path(path)
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield MarketBar(
                timestamp_utc=row[column_map.timestamp],
                symbol=row[column_map.symbol],
                open=float(row[column_map.open]),
                high=float(row[column_map.high]),
                low=float(row[column_map.low]),
                close=float(row[column_map.close]),
                volume=float(row[column_map.volume]),
            )


def profile_csv_bars(path: str | Path, column_map: CsvBarColumnMap) -> CsvBarProfile:
    row_count = 0
    start_timestamp_utc = ""
    end_timestamp_utc = ""
    symbols: set[str] = set()
    input_path = Path(path)
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            timestamp = row[column_map.timestamp]
            row_count += 1
            if row_count == 1:
                start_timestamp_utc = timestamp
            end_timestamp_utc = timestamp
            symbols.add(row[column_map.symbol])
    return CsvBarProfile(
        row_count=row_count,
        start_timestamp_utc=start_timestamp_utc,
        end_timestamp_utc=end_timestamp_utc,
        symbols=tuple(sorted(symbols)),
    )
