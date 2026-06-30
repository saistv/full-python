from __future__ import annotations

import csv
from dataclasses import dataclass
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


def load_csv_bars(path: str | Path, column_map: CsvBarColumnMap) -> list[MarketBar]:
    input_path = Path(path)
    bars: list[MarketBar] = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            bars.append(
                MarketBar(
                    timestamp_utc=row[column_map.timestamp],
                    symbol=row[column_map.symbol],
                    open=float(row[column_map.open]),
                    high=float(row[column_map.high]),
                    low=float(row[column_map.low]),
                    close=float(row[column_map.close]),
                    volume=float(row[column_map.volume]),
                )
            )
    return bars
