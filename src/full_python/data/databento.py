from __future__ import annotations

import csv
import io
from pathlib import Path

import zstandard

from full_python.models import MarketBar


REQUIRED_OHLCV_COLUMNS = {
    "ts_event",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "symbol",
}


def load_databento_ohlcv_bars(
    path: str | Path,
    symbol_root: str = "NQ",
    include_spreads: bool = False,
) -> list[MarketBar]:
    input_path = Path(path)
    bars: list[MarketBar] = []
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
                if not include_spreads and "-" in symbol:
                    continue
                bars.append(
                    MarketBar(
                        timestamp_utc=_normalize_timestamp(row["ts_event"]),
                        symbol=symbol,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    )
                )
    return sorted(bars, key=lambda bar: (bar.timestamp_utc, bar.symbol))


def _validate_columns(fieldnames: list[str] | None) -> None:
    available = set(fieldnames or [])
    missing = sorted(REQUIRED_OHLCV_COLUMNS - available)
    if missing:
        raise ValueError(f"Missing required Databento OHLCV columns: {', '.join(missing)}")


def _normalize_timestamp(timestamp: str) -> str:
    if timestamp.endswith(".000000000Z"):
        return f"{timestamp[:-11]}Z"
    return timestamp
