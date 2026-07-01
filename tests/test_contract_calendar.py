from full_python.data.contract_calendar import build_dominant_contract_calendar
from full_python.data.inventory import DatabentoFileInventory, SymbolInventory


def test_build_dominant_contract_calendar_selects_highest_row_count_outright() -> None:
    file_inventory = DatabentoFileInventory(
        path="/data/glbx-mdp3-20250203.ohlcv-1m.csv.zst",
        file_size_bytes=123,
        symbols={
            "NQH5": SymbolInventory(100, "2025-02-03T00:00:00Z", "2025-02-03T23:59:00Z"),
            "NQM5": SymbolInventory(200, "2025-02-03T00:00:00Z", "2025-02-03T23:59:00Z"),
            "NQH5-NQM5": SymbolInventory(300, "2025-02-03T00:00:00Z", "2025-02-03T23:59:00Z"),
        },
    )

    calendar = build_dominant_contract_calendar([file_inventory])

    assert calendar.symbol_root == "NQ"
    assert calendar.selection_rule == "dominant_outright_row_count"
    assert len(calendar.entries) == 1
    entry = calendar.entries[0]
    assert entry.file_path == "/data/glbx-mdp3-20250203.ohlcv-1m.csv.zst"
    assert entry.trading_date == "2025-02-03"
    assert entry.selected_contract == "NQM5"
    assert entry.selection_rule == "dominant_outright_row_count"
    assert [candidate.symbol for candidate in entry.candidates] == ["NQM5", "NQH5"]


def test_build_dominant_contract_calendar_tie_breaks_by_symbol() -> None:
    file_inventory = DatabentoFileInventory(
        path="/data/glbx-mdp3-20250204.ohlcv-1m.csv.zst",
        file_size_bytes=123,
        symbols={
            "NQM5": SymbolInventory(200, "2025-02-04T00:00:00Z", "2025-02-04T23:59:00Z"),
            "NQH5": SymbolInventory(200, "2025-02-04T00:00:00Z", "2025-02-04T23:59:00Z"),
        },
    )

    calendar = build_dominant_contract_calendar([file_inventory])

    assert calendar.entries[0].selected_contract == "NQH5"
    assert [candidate.symbol for candidate in calendar.entries[0].candidates] == ["NQH5", "NQM5"]


def test_build_dominant_contract_calendar_handles_no_outright_candidates() -> None:
    file_inventory = DatabentoFileInventory(
        path="/data/glbx-mdp3-20250205.ohlcv-1m.csv.zst",
        file_size_bytes=123,
        symbols={
            "NQH5-NQM5": SymbolInventory(300, "2025-02-05T00:00:00Z", "2025-02-05T23:59:00Z"),
        },
    )

    calendar = build_dominant_contract_calendar([file_inventory])

    assert calendar.entries[0].selected_contract is None
    assert calendar.entries[0].candidates == []
