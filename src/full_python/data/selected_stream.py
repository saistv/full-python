from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from full_python.data.contract_calendar import ContractCalendar
from full_python.data.databento import load_databento_ohlcv_bars


SELECTED_STREAM_COLUMNS = [
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source_file",
    "trading_date",
    "selected_contract",
    "selection_rule",
]


@dataclass(frozen=True)
class SelectedContractBar:
    timestamp_utc: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_file: str
    trading_date: str
    selected_contract: str
    selection_rule: str

    def to_csv_row(self) -> dict[str, str | float]:
        return {
            "timestamp": self.timestamp_utc,
            "symbol": self.symbol,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "source_file": self.source_file,
            "trading_date": self.trading_date,
            "selected_contract": self.selected_contract,
            "selection_rule": self.selection_rule,
        }


@dataclass(frozen=True)
class SelectedContractStream:
    rows: list[SelectedContractBar]
    skipped_entries: list[dict[str, str]]


def build_selected_contract_stream(calendar: ContractCalendar) -> SelectedContractStream:
    rows: list[SelectedContractBar] = []
    skipped_entries: list[dict[str, str]] = []
    for entry in calendar.entries:
        if entry.selected_contract is None:
            skipped_entries.append(
                {
                    "file_path": entry.file_path,
                    "trading_date": entry.trading_date,
                    "reason": "no_selected_contract",
                }
            )
            continue

        bars = load_databento_ohlcv_bars(
            entry.file_path,
            symbol_root=calendar.symbol_root,
            contract_symbol=entry.selected_contract,
        )
        for bar in bars:
            rows.append(
                SelectedContractBar(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    source_file=entry.file_path,
                    trading_date=entry.trading_date,
                    selected_contract=entry.selected_contract,
                    selection_rule=entry.selection_rule,
                )
            )
    rows.sort(key=lambda row: (row.timestamp_utc, row.source_file, row.symbol))
    return SelectedContractStream(rows=rows, skipped_entries=skipped_entries)


def write_selected_contract_stream_csv(
    stream: SelectedContractStream,
    path: str | Path,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SELECTED_STREAM_COLUMNS)
        writer.writeheader()
        for row in stream.rows:
            writer.writerow(row.to_csv_row())


def write_selected_contract_stream_manifest(
    stream: SelectedContractStream,
    path: str | Path,
    calendar: ContractCalendar,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = selected_contract_stream_manifest(stream, calendar)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def selected_contract_stream_manifest(
    stream: SelectedContractStream,
    calendar: ContractCalendar,
) -> dict[str, Any]:
    selected_contracts = sorted({row.selected_contract for row in stream.rows})
    source_files = sorted({row.source_file for row in stream.rows})
    return {
        "source_format": "databento-ohlcv",
        "stream_format": "selected_contract_csv",
        "symbol_root": calendar.symbol_root,
        "selection_rule": calendar.selection_rule,
        "calendar_entry_count": len(calendar.entries),
        "source_file_count": len(source_files),
        "selected_contract_count": len(selected_contracts),
        "selected_contracts": selected_contracts,
        "row_count": len(stream.rows),
        "start_timestamp_utc": stream.rows[0].timestamp_utc if stream.rows else None,
        "end_timestamp_utc": stream.rows[-1].timestamp_utc if stream.rows else None,
        "skipped_entries": stream.skipped_entries,
        "columns": list(SELECTED_STREAM_COLUMNS),
        "calendar": calendar.to_dict(),
    }
