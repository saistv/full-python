from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any

from full_python.data.inventory import DatabentoFileInventory


DOMINANT_OUTRIGHT_RULE = "dominant_outright_row_count"


@dataclass(frozen=True)
class ContractCandidate:
    symbol: str
    row_count: int
    start_timestamp_utc: str
    end_timestamp_utc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContractCalendarEntry:
    file_path: str
    trading_date: str
    selected_contract: str | None
    selection_rule: str
    candidates: list[ContractCandidate]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "trading_date": self.trading_date,
            "selected_contract": self.selected_contract,
            "selection_rule": self.selection_rule,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class ContractCalendar:
    symbol_root: str
    selection_rule: str
    entries: list[ContractCalendarEntry]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol_root": self.symbol_root,
            "selection_rule": self.selection_rule,
            "entries": [entry.to_dict() for entry in self.entries],
        }


def build_dominant_contract_calendar(
    inventories: list[DatabentoFileInventory],
    symbol_root: str = "NQ",
) -> ContractCalendar:
    entries = [
        _build_entry(inventory)
        for inventory in inventories
    ]
    return ContractCalendar(
        symbol_root=symbol_root,
        selection_rule=DOMINANT_OUTRIGHT_RULE,
        entries=entries,
    )


def _build_entry(inventory: DatabentoFileInventory) -> ContractCalendarEntry:
    candidates = _outright_candidates(inventory)
    selected_contract = candidates[0].symbol if candidates else None
    return ContractCalendarEntry(
        file_path=inventory.path,
        trading_date=_trading_date_from_path(inventory.path),
        selected_contract=selected_contract,
        selection_rule=DOMINANT_OUTRIGHT_RULE,
        candidates=candidates,
    )


def _outright_candidates(inventory: DatabentoFileInventory) -> list[ContractCandidate]:
    candidates = [
        ContractCandidate(
            symbol=symbol,
            row_count=symbol_inventory.row_count,
            start_timestamp_utc=symbol_inventory.start_timestamp_utc,
            end_timestamp_utc=symbol_inventory.end_timestamp_utc,
        )
        for symbol, symbol_inventory in inventory.symbols.items()
        if "-" not in symbol
    ]
    return sorted(candidates, key=lambda candidate: (-candidate.row_count, candidate.symbol))


def _trading_date_from_path(path: str) -> str:
    file_name = Path(path).name
    match = re.search(r"(\d{8})", file_name)
    if not match:
        return ""
    raw_date = match.group(1)
    return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
