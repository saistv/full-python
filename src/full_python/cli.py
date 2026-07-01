from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from full_python.data.databento import load_databento_ohlcv_bars
from full_python.data.contract_calendar import build_dominant_contract_calendar
from full_python.data.inventory import inspect_databento_ohlcv_folder
from full_python.data.loaders import CsvBarColumnMap, iter_csv_bars, load_csv_bars, profile_csv_bars
from full_python.data.manifest import DataManifest, file_sha256
from full_python.data.sessions import filter_bars_by_session
from full_python.data.selected_stream import (
    build_selected_contract_stream,
    write_selected_contract_stream_csv,
    write_selected_contract_stream_manifest,
)
from full_python.events import StreamingEventLedger
from full_python.execution.simulator import (
    ExitConversionConfig,
    SimulationCosts,
    simulate_strategy_trades,
    write_trade_summary_json,
    write_trades_csv,
)
from full_python.replay import ReplayEngine
from full_python.reporting.survivability import build_survivability_report
from full_python.reporting.trade_analysis import (
    build_trade_analysis,
    load_trade_csv,
    write_trade_analysis_json,
)
from full_python.strategy.baseline import BaselineMomentumStrategy
from full_python.strategy.config import BaselineMomentumConfig


def run_baseline(
    *,
    data_path: str | Path,
    output_dir: str | Path,
    source_format: str = "csv",
    symbol_root: str = "NQ",
    contract_symbol: str | None = None,
    include_spreads: bool = False,
    stream_events: bool = False,
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
        if stream_events:
            profile = profile_csv_bars(input_path, column_map)
            bars = iter_csv_bars(input_path, column_map)
        else:
            loaded_bars = load_csv_bars(input_path, column_map)
            profile = None
            bars = loaded_bars
        manifest_column_map = asdict(column_map)
    elif source_format == "databento-ohlcv":
        if stream_events:
            raise ValueError("stream_events is currently supported only for csv input")
        loaded_bars = load_databento_ohlcv_bars(
            input_path,
            symbol_root=symbol_root,
            contract_symbol=contract_symbol,
            include_spreads=include_spreads,
        )
        profile = None
        bars = loaded_bars
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

    if profile is not None:
        if profile.row_count == 0:
            raise ValueError(f"No bars loaded from {input_path}")
        row_count = profile.row_count
        start_timestamp_utc = profile.start_timestamp_utc
        end_timestamp_utc = profile.end_timestamp_utc
        contract = profile.symbols[0] if len(profile.symbols) == 1 else "MULTI"
        replay_bars = bars
    else:
        bar_list = list(bars)
        if not bar_list:
            raise ValueError(f"No bars loaded from {input_path}")
        row_count = len(bar_list)
        start_timestamp_utc = bar_list[0].timestamp_utc
        end_timestamp_utc = bar_list[-1].timestamp_utc
        symbols = {bar.symbol for bar in bar_list}
        contract = bar_list[0].symbol if len(symbols) == 1 else "MULTI"
        replay_bars = bar_list

    if row_count == 0:
        raise ValueError(f"No bars loaded from {input_path}")

    manifest = DataManifest(
        dataset_name=input_path.stem,
        source=source_format,
        symbol=symbol_root,
        contract=contract,
        timezone="UTC",
        session="UNKNOWN",
        start_timestamp_utc=start_timestamp_utc,
        end_timestamp_utc=end_timestamp_utc,
        path=str(input_path),
        content_sha256=file_sha256(input_path),
        row_count=row_count,
        file_size_bytes=input_path.stat().st_size,
        column_map=manifest_column_map,
    )
    config = BaselineMomentumConfig()
    strategy = BaselineMomentumStrategy(config)
    events_path = run_dir / "events.jsonl"
    if stream_events:
        ledger = StreamingEventLedger(events_path)
        try:
            ReplayEngine().run(replay_bars, strategy, ledger=ledger)
        finally:
            ledger.close()
    else:
        ledger = ReplayEngine().run(replay_bars, strategy)
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
        "event_count": ledger.event_count,
        "survivability": survivability.to_dict(),
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def run_databento_inventory(
    *,
    folder: str | Path,
    output_dir: str | Path,
    symbol_root: str = "NQ",
    markdown: bool = False,
) -> Path:
    inventories = inspect_databento_ohlcv_folder(folder, symbol_root=symbol_root)
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_format": "databento-ohlcv",
        "symbol_root": symbol_root,
        "folder": str(Path(folder)),
        "files": [inventory.to_dict() for inventory in inventories],
    }
    json_path = run_dir / "contract_inventory.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if markdown:
        markdown_path = run_dir / "contract_inventory.md"
        markdown_path.write_text(_render_inventory_markdown(payload), encoding="utf-8")
    return json_path


def run_contract_calendar(
    *,
    folder: str | Path,
    output_dir: str | Path,
    symbol_root: str = "NQ",
    markdown: bool = False,
) -> Path:
    inventories = inspect_databento_ohlcv_folder(folder, symbol_root=symbol_root)
    calendar = build_dominant_contract_calendar(inventories, symbol_root=symbol_root)
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        **calendar.to_dict(),
        "source_format": "databento-ohlcv",
        "folder": str(Path(folder)),
    }
    json_path = run_dir / "contract_calendar.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if markdown:
        markdown_path = run_dir / "contract_calendar.md"
        markdown_path.write_text(_render_contract_calendar_markdown(payload), encoding="utf-8")
    return json_path


def run_selected_stream(
    *,
    folder: str | Path,
    output_dir: str | Path,
    symbol_root: str = "NQ",
) -> Path:
    inventories = inspect_databento_ohlcv_folder(folder, symbol_root=symbol_root)
    calendar = build_dominant_contract_calendar(inventories, symbol_root=symbol_root)
    stream = build_selected_contract_stream(calendar)
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "selected_bars.csv"
    manifest_path = run_dir / "selected_bars_manifest.json"
    write_selected_contract_stream_csv(stream, csv_path)
    write_selected_contract_stream_manifest(stream, manifest_path, calendar)
    return csv_path


def run_baseline_trade_simulation(
    *,
    data_path: str | Path,
    output_dir: str | Path,
    stream_input: bool = False,
    session: str = "all",
    point_value: float = 2.0,
    slippage_points_per_side: float = 1.0,
    commission_per_contract: float = 1.0,
    symbol_change_exit_mode: str = "next_open",
    mfe_trailing_activation_points: float | None = None,
    mfe_trailing_giveback_points: float | None = None,
) -> Path:
    input_path = Path(data_path)
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    column_map = CsvBarColumnMap(
        timestamp="timestamp",
        symbol="symbol",
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
    )
    bars = iter_csv_bars(input_path, column_map) if stream_input else load_csv_bars(input_path, column_map)
    session_bars = filter_bars_by_session(bars, session)
    strategy = BaselineMomentumStrategy(BaselineMomentumConfig())
    costs = SimulationCosts(
        point_value=point_value,
        slippage_points_per_side=slippage_points_per_side,
        commission_per_contract=commission_per_contract,
    )
    exit_conversion = ExitConversionConfig(
        mfe_trailing_activation_points=mfe_trailing_activation_points,
        mfe_trailing_giveback_points=mfe_trailing_giveback_points,
    )
    ledger = simulate_strategy_trades(
        session_bars,
        strategy,
        costs=costs,
        symbol_change_exit_mode=symbol_change_exit_mode,
        exit_conversion=exit_conversion,
    )
    ledger.assumptions["session"] = session
    trades_path = run_dir / "trades.csv"
    summary_path = run_dir / "trade_summary.json"
    write_trades_csv(ledger, trades_path)
    write_trade_summary_json(ledger, summary_path)
    return trades_path


def run_trade_analysis(
    *,
    trades_path: str | Path,
    output_dir: str | Path,
) -> Path:
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    trades = load_trade_csv(trades_path)
    analysis = build_trade_analysis(trades)
    analysis_path = run_dir / "trade_analysis.json"
    write_trade_analysis_json(analysis, analysis_path)
    return analysis_path


def _render_inventory_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# Databento Contract Inventory",
        "",
        f"Folder: `{payload['folder']}`",
        "",
        "| File | Symbol | Rows | Start UTC | End UTC |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for file_inventory in payload["files"]:
        file_payload = file_inventory
        assert isinstance(file_payload, dict)
        file_name = Path(str(file_payload["path"])).name
        symbols = file_payload["symbols"]
        assert isinstance(symbols, dict)
        if not symbols:
            lines.append(f"| {file_name} |  | 0 |  |  |")
        for symbol, symbol_payload in symbols.items():
            assert isinstance(symbol_payload, dict)
            lines.append(
                "| "
                + " | ".join(
                    [
                        file_name,
                        str(symbol),
                        str(symbol_payload["row_count"]),
                        str(symbol_payload["start_timestamp_utc"]),
                        str(symbol_payload["end_timestamp_utc"]),
                    ]
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def _render_contract_calendar_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# Databento Contract Calendar",
        "",
        f"Folder: `{payload['folder']}`",
        "",
        f"Selection rule: `{payload['selection_rule']}`",
        "",
        "| Trading Date | Selected Contract | File | Candidate Count |",
        "| --- | --- | --- | ---: |",
    ]
    for entry_payload in payload["entries"]:
        assert isinstance(entry_payload, dict)
        file_name = Path(str(entry_payload["file_path"])).name
        candidates = entry_payload["candidates"]
        assert isinstance(candidates, list)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(entry_payload["trading_date"]),
                    str(entry_payload["selected_contract"] or ""),
                    file_name,
                    str(len(candidates)),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def run_inventory_databento_command(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(description="Inventory Databento OHLCV files.")
    parser.add_argument("--folder", required=True, help="Folder containing .ohlcv-1m.csv.zst files")
    parser.add_argument("--output-dir", required=True, help="Directory for contract_inventory outputs")
    parser.add_argument(
        "--symbol-root",
        default="NQ",
        help="Symbol root to include in the inventory",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Also write contract_inventory.md",
    )
    args = parser.parse_args(argv)
    return run_databento_inventory(
        folder=args.folder,
        output_dir=args.output_dir,
        symbol_root=args.symbol_root,
        markdown=args.markdown,
    )


def run_build_contract_calendar_command(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(description="Build a Databento dominant contract calendar.")
    parser.add_argument("--folder", required=True, help="Folder containing .ohlcv-1m.csv.zst files")
    parser.add_argument("--output-dir", required=True, help="Directory for contract_calendar outputs")
    parser.add_argument(
        "--symbol-root",
        default="NQ",
        help="Symbol root to include in the calendar",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Also write contract_calendar.md",
    )
    args = parser.parse_args(argv)
    return run_contract_calendar(
        folder=args.folder,
        output_dir=args.output_dir,
        symbol_root=args.symbol_root,
        markdown=args.markdown,
    )


def run_build_selected_stream_command(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(description="Build a selected-contract Databento bar stream.")
    parser.add_argument("--folder", required=True, help="Folder containing .ohlcv-1m.csv.zst files")
    parser.add_argument("--output-dir", required=True, help="Directory for selected stream outputs")
    parser.add_argument(
        "--symbol-root",
        default="NQ",
        help="Symbol root to include in the selected stream",
    )
    args = parser.parse_args(argv)
    return run_selected_stream(
        folder=args.folder,
        output_dir=args.output_dir,
        symbol_root=args.symbol_root,
    )


def run_simulate_baseline_trades_command(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(description="Simulate first-pass baseline trades from CSV bars.")
    parser.add_argument("--data", required=True, help="Input CSV market-bar data file")
    parser.add_argument("--output-dir", required=True, help="Directory for trades.csv and trade_summary.json")
    parser.add_argument(
        "--stream-input",
        action="store_true",
        help="Stream CSV bars instead of loading the whole input into memory",
    )
    parser.add_argument(
        "--session",
        choices=["all", "rth"],
        default="all",
        help="Session filter for trade simulation",
    )
    parser.add_argument(
        "--point-value",
        type=float,
        default=2.0,
        help="Dollar value per point per contract",
    )
    parser.add_argument(
        "--slippage-points-per-side",
        type=float,
        default=1.0,
        help="Slippage in points applied to entry and exit",
    )
    parser.add_argument(
        "--commission-per-contract",
        type=float,
        default=1.0,
        help="Commission dollars per contract per side",
    )
    parser.add_argument(
        "--symbol-change-exit-mode",
        choices=["next_open", "previous_close"],
        default="next_open",
        help="How to close an open trade when the selected contract changes",
    )
    parser.add_argument(
        "--mfe-trailing-activation-points",
        type=float,
        default=None,
        help="Enable MFE trailing after this many favorable points",
    )
    parser.add_argument(
        "--mfe-trailing-giveback-points",
        type=float,
        default=None,
        help="Trail by this many points after MFE trailing activation",
    )
    args = parser.parse_args(argv)
    return run_baseline_trade_simulation(
        data_path=args.data,
        output_dir=args.output_dir,
        stream_input=args.stream_input,
        session=args.session,
        point_value=args.point_value,
        slippage_points_per_side=args.slippage_points_per_side,
        commission_per_contract=args.commission_per_contract,
        symbol_change_exit_mode=args.symbol_change_exit_mode,
        mfe_trailing_activation_points=args.mfe_trailing_activation_points,
        mfe_trailing_giveback_points=args.mfe_trailing_giveback_points,
    )


def run_analyze_trades_command(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(description="Analyze a generated trades.csv ledger.")
    parser.add_argument("--trades", required=True, help="Input trades.csv file")
    parser.add_argument("--output-dir", required=True, help="Directory for trade_analysis.json")
    args = parser.parse_args(argv)
    return run_trade_analysis(
        trades_path=args.trades,
        output_dir=args.output_dir,
    )


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "inventory-databento":
        inventory_path = run_inventory_databento_command(sys.argv[2:])
        print(inventory_path)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "build-contract-calendar":
        calendar_path = run_build_contract_calendar_command(sys.argv[2:])
        print(calendar_path)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "build-selected-stream":
        csv_path = run_build_selected_stream_command(sys.argv[2:])
        print(csv_path)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "simulate-baseline-trades":
        trades_path = run_simulate_baseline_trades_command(sys.argv[2:])
        print(trades_path)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "analyze-trades":
        analysis_path = run_analyze_trades_command(sys.argv[2:])
        print(analysis_path)
        return

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
        "--contract-symbol",
        default=None,
        help="Exact Databento contract symbol to load, such as NQH5 or NQU2026",
    )
    parser.add_argument(
        "--include-spreads",
        action="store_true",
        help="Include Databento spread symbols containing '-'",
    )
    parser.add_argument(
        "--stream-events",
        action="store_true",
        help="Write events incrementally instead of storing the full event ledger in memory",
    )
    args = parser.parse_args()
    report_path = run_baseline(
        data_path=args.data,
        output_dir=args.output_dir,
        source_format=args.source_format,
        symbol_root=args.symbol_root,
        contract_symbol=args.contract_symbol,
        include_spreads=args.include_spreads,
        stream_events=args.stream_events,
    )
    print(report_path)


if __name__ == "__main__":
    main()
