from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path

from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.manifest import DataManifest, file_sha256
from full_python.data.validation import validate_bars
from full_python.reporting.survivability import (
    TradeResult,
    build_daily_metrics,
    build_monthly_breakdown,
    build_survivability_report,
)
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.baseline import BaselineMomentumStrategy
from full_python.strategy.config import BaselineMomentumConfig

TRADE_CSV_COLUMNS = [
    "symbol",
    "side",
    "quantity",
    "entry_timestamp_utc",
    "entry_price",
    "exit_timestamp_utc",
    "exit_price",
    "exit_reason",
    "stop_price",
    "gross_points",
    "gross_pnl",
    "commission",
    "net_pnl",
    "mfe_points",
    "mae_points",
    "session_date",
    "ambiguous_exit",
]


def run_baseline(
    *,
    data_path: str | Path,
    output_dir: str | Path,
    fill_timing: str = "next_bar_open",
    allow_dirty_data: bool = False,
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
    bars = load_csv_bars(input_path, column_map)
    if not bars:
        raise ValueError(f"No bars loaded from {input_path}")

    quality = validate_bars(bars)
    if not quality.is_structurally_clean and not allow_dirty_data:
        raise ValueError(
            "Data failed structural validation "
            f"({quality.structural_issue_count} issues: {quality.issue_counts}). "
            "Fix the data or rerun with --allow-dirty-data to proceed anyway."
        )

    manifest = DataManifest(
        dataset_name=input_path.stem,
        source="csv",
        symbol=bars[0].symbol,
        contract=bars[0].symbol,
        timezone="UTC",
        session="ALL",
        start_timestamp_utc=bars[0].timestamp_utc,
        end_timestamp_utc=bars[-1].timestamp_utc,
        path=str(input_path),
        content_sha256=file_sha256(input_path),
        row_count=len(bars),
        file_size_bytes=input_path.stat().st_size,
        column_map=asdict(column_map),
    )
    strategy_config = BaselineMomentumConfig()
    simulation_config = SimulationConfig(fill_timing=fill_timing)
    strategy = BaselineMomentumStrategy(strategy_config)
    result = SimulationEngine(simulation_config).run(bars, strategy)

    # Deterministic run identity: same data + same configs => same run id.
    run_id = "-".join(
        [
            manifest.stable_hash()[:8],
            strategy_config.parameter_hash()[:8],
            simulation_config.parameter_hash()[:8],
        ]
    )

    events_path = run_dir / "events.jsonl"
    result.ledger.write_jsonl(events_path)

    trades_path = run_dir / "trades.csv"
    with trades_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_CSV_COLUMNS)
        writer.writeheader()
        for trade in result.trades:
            writer.writerow(trade.to_payload())

    daily_pnl: dict[str, float] = {}
    for trade in result.trades:
        daily_pnl[trade.session_date] = daily_pnl.get(trade.session_date, 0.0) + trade.net_pnl

    daily_path = run_dir / "daily_pnl.csv"
    cumulative = 0.0
    with daily_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["session_date", "net_pnl", "cumulative_pnl"])
        for day in result.session_dates:
            pnl = daily_pnl.get(day, 0.0)
            cumulative += pnl
            writer.writerow([day, f"{pnl:.2f}", f"{cumulative:.2f}"])

    survivability = build_survivability_report(
        [
            TradeResult(
                exit_timestamp_utc=trade.exit_timestamp_utc,
                side=trade.side,
                pnl=trade.net_pnl,
            )
            for trade in result.trades
        ]
    )
    daily_metrics = build_daily_metrics(daily_pnl, list(result.session_dates))
    monthly = build_monthly_breakdown(daily_pnl)
    exit_reasons: dict[str, int] = {}
    ambiguous_exits = 0
    for trade in result.trades:
        exit_reasons[trade.exit_reason] = exit_reasons.get(trade.exit_reason, 0) + 1
        if trade.ambiguous_exit:
            ambiguous_exits += 1

    report = {
        "run_id": run_id,
        "data": {
            **manifest.to_dict(),
            "manifest_hash": manifest.stable_hash(),
        },
        "quality": quality.to_dict(),
        "strategy": {
            **strategy_config.to_dict(),
            "parameter_hash": strategy_config.parameter_hash(),
        },
        "simulation": {
            **simulation_config.to_dict(),
            "parameter_hash": simulation_config.parameter_hash(),
        },
        "events_path": str(events_path),
        "trades_path": str(trades_path),
        "daily_pnl_path": str(daily_path),
        "survivability": survivability.to_dict(),
        "daily": daily_metrics.to_dict(),
        "monthly": monthly,
        "exit_reasons": exit_reasons,
        "ambiguous_exits": ambiguous_exits,
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Full Python baseline replay.")
    parser.add_argument("--data", required=True, help="CSV file with timestamp,symbol,open,high,low,close,volume columns")
    parser.add_argument("--output-dir", required=True, help="Directory for report.json, events.jsonl, trades.csv, daily_pnl.csv")
    parser.add_argument(
        "--fill-timing",
        default="next_bar_open",
        choices=["next_bar_open", "signal_bar_close"],
        help="signal_bar_close exists only for legacy TradingView reconciliation",
    )
    parser.add_argument(
        "--allow-dirty-data",
        action="store_true",
        help="Proceed despite structural data-quality issues (they are still reported)",
    )
    args = parser.parse_args()
    report_path = run_baseline(
        data_path=args.data,
        output_dir=args.output_dir,
        fill_timing=args.fill_timing,
        allow_dirty_data=args.allow_dirty_data,
    )
    print(report_path)


if __name__ == "__main__":
    main()
