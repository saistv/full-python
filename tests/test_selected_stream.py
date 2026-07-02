import csv
import json
from pathlib import Path

import zstandard

from full_python.data.contract_calendar import (
    ContractCalendar,
    ContractCalendarEntry,
    ContractCandidate,
    DOMINANT_OUTRIGHT_RULE,
)
from full_python.data.selected_stream import (
    build_selected_contract_stream,
    write_selected_contract_stream_csv,
    write_selected_contract_stream_manifest,
)


def write_zst_csv(path: Path, content: str) -> None:
    compressor = zstandard.ZstdCompressor()
    path.write_bytes(compressor.compress(content.encode("utf-8")))


def test_build_selected_contract_stream_loads_only_selected_contracts(tmp_path: Path) -> None:
    first_file = tmp_path / "glbx-mdp3-20250203.ohlcv-1m.csv.zst"
    second_file = tmp_path / "glbx-mdp3-20250204.ohlcv-1m.csv.zst"
    write_zst_csv(
        first_file,
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
        "2025-02-03T00:00:00.000000000Z,33,1,1,100,101,99,100.5,10,NQH5\n"
        "2025-02-03T00:00:00.000000000Z,33,1,2,200,201,199,200.5,20,NQM5\n",
    )
    write_zst_csv(
        second_file,
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
        "2025-02-04T00:00:00.000000000Z,33,1,1,300,301,299,300.5,30,NQM5\n",
    )
    calendar = ContractCalendar(
        symbol_root="NQ",
        selection_rule=DOMINANT_OUTRIGHT_RULE,
        entries=[
            ContractCalendarEntry(
                file_path=str(first_file),
                trading_date="2025-02-03",
                selected_contract="NQH5",
                selection_rule=DOMINANT_OUTRIGHT_RULE,
                candidates=[
                    ContractCandidate("NQH5", 1, "2025-02-03T00:00:00Z", "2025-02-03T00:00:00Z")
                ],
            ),
            ContractCalendarEntry(
                file_path=str(second_file),
                trading_date="2025-02-04",
                selected_contract="NQM5",
                selection_rule=DOMINANT_OUTRIGHT_RULE,
                candidates=[
                    ContractCandidate("NQM5", 1, "2025-02-04T00:00:00Z", "2025-02-04T00:00:00Z")
                ],
            ),
        ],
    )

    stream = build_selected_contract_stream(calendar)

    assert [row.symbol for row in stream.rows] == ["NQH5", "NQM5"]
    assert [row.close for row in stream.rows] == [100.5, 300.5]
    assert stream.rows[0].source_file == str(first_file)
    assert stream.rows[0].trading_date == "2025-02-03"
    assert stream.rows[0].selected_contract == "NQH5"
    assert stream.rows[0].selection_rule == DOMINANT_OUTRIGHT_RULE
    assert stream.skipped_entries == []


def test_build_selected_contract_stream_records_skipped_entries(tmp_path: Path) -> None:
    calendar = ContractCalendar(
        symbol_root="NQ",
        selection_rule=DOMINANT_OUTRIGHT_RULE,
        entries=[
            ContractCalendarEntry(
                file_path=str(tmp_path / "glbx-mdp3-20250205.ohlcv-1m.csv.zst"),
                trading_date="2025-02-05",
                selected_contract=None,
                selection_rule=DOMINANT_OUTRIGHT_RULE,
                candidates=[],
            )
        ],
    )

    stream = build_selected_contract_stream(calendar)

    assert stream.rows == []
    assert stream.skipped_entries == [
        {
            "file_path": str(tmp_path / "glbx-mdp3-20250205.ohlcv-1m.csv.zst"),
            "trading_date": "2025-02-05",
            "reason": "no_selected_contract",
        }
    ]


def test_write_selected_contract_stream_csv_and_manifest(tmp_path: Path) -> None:
    data_file = tmp_path / "glbx-mdp3-20250203.ohlcv-1m.csv.zst"
    write_zst_csv(
        data_file,
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
        "2025-02-03T00:00:00.000000000Z,33,1,1,100,101,99,100.5,10,NQH5\n",
    )
    calendar = ContractCalendar(
        symbol_root="NQ",
        selection_rule=DOMINANT_OUTRIGHT_RULE,
        entries=[
            ContractCalendarEntry(
                file_path=str(data_file),
                trading_date="2025-02-03",
                selected_contract="NQH5",
                selection_rule=DOMINANT_OUTRIGHT_RULE,
                candidates=[
                    ContractCandidate("NQH5", 1, "2025-02-03T00:00:00Z", "2025-02-03T00:00:00Z")
                ],
            )
        ],
    )
    stream = build_selected_contract_stream(calendar)

    csv_path = tmp_path / "selected_bars.csv"
    manifest_path = tmp_path / "selected_bars_manifest.json"
    write_selected_contract_stream_csv(stream, csv_path)
    write_selected_contract_stream_manifest(stream, manifest_path, calendar)

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["timestamp"] == "2025-02-03T00:00:00Z"
    assert rows[0]["source_file"] == str(data_file)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["row_count"] == 1
    assert manifest["symbol_root"] == "NQ"
    assert manifest["selection_rule"] == DOMINANT_OUTRIGHT_RULE
