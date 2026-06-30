from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from full_python.data.databento import load_databento_ohlcv_bars
from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.manifest import DataManifest, file_sha256
from full_python.replay import ReplayEngine
from full_python.reporting.survivability import build_survivability_report
from full_python.strategy.baseline import BaselineMomentumStrategy
from full_python.strategy.config import BaselineMomentumConfig


def run_baseline(
    *,
    data_path: str | Path,
    output_dir: str | Path,
    source_format: str = "csv",
    symbol_root: str = "NQ",
    include_spreads: bool = False,
) -> Path:
    input_path = Path(data_path)
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if source_format == "csv":
        column_map = CsvBarColumnMap(
            timestamp="timestamp",
            symbol="symbol",
            open="open",
            high="high",
            low="low",
            close="close",
            volume="volume",
        )
        bars = load_csv_bars(input_path, column_map)
        manifest_column_map = asdict(column_map)
    elif source_format == "databento-ohlcv":
        bars = load_databento_ohlcv_bars(
            input_path,
            symbol_root=symbol_root,
            include_spreads=include_spreads,
        )
        manifest_column_map = {
            "timestamp": "ts_event",
            "symbol": "symbol",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
    else:
        raise ValueError(f"Unsupported source format: {source_format}")
    if not bars:
        raise ValueError(f"No bars loaded from {input_path}")

    manifest = DataManifest(
        dataset_name=input_path.stem,
        source=source_format,
        symbol=symbol_root,
        contract=bars[0].symbol,
        timezone="UTC",
        session="UNKNOWN",
        start_timestamp_utc=bars[0].timestamp_utc,
        end_timestamp_utc=bars[-1].timestamp_utc,
        path=str(input_path),
        content_sha256=file_sha256(input_path),
        row_count=len(bars),
        file_size_bytes=input_path.stat().st_size,
        column_map=manifest_column_map,
    )
    config = BaselineMomentumConfig()
    strategy = BaselineMomentumStrategy(config)
    ledger = ReplayEngine().run(bars, strategy)
    events_path = run_dir / "events.jsonl"
    ledger.write_jsonl(events_path)

    survivability = build_survivability_report([])
    report = {
        "data": {
            **manifest.to_dict(),
            "manifest_hash": manifest.stable_hash(),
        },
        "strategy": {
            **config.to_dict(),
            "parameter_hash": config.parameter_hash(),
        },
        "events_path": str(events_path),
        "survivability": survivability.to_dict(),
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Full Python baseline replay.")
    parser.add_argument("--data", required=True, help="Input market-bar data file")
    parser.add_argument("--output-dir", required=True, help="Directory for report.json and events.jsonl")
    parser.add_argument(
        "--source-format",
        choices=["csv", "databento-ohlcv"],
        default="csv",
        help="Input bar format",
    )
    parser.add_argument(
        "--symbol-root",
        default="NQ",
        help="Symbol root to include for Databento OHLCV input",
    )
    parser.add_argument(
        "--include-spreads",
        action="store_true",
        help="Include Databento spread symbols containing '-'",
    )
    args = parser.parse_args()
    report_path = run_baseline(
        data_path=args.data,
        output_dir=args.output_dir,
        source_format=args.source_format,
        symbol_root=args.symbol_root,
        include_spreads=args.include_spreads,
    )
    print(report_path)


if __name__ == "__main__":
    main()
