from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import subprocess

from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.manifest import DataManifest, file_sha256
from full_python.data.validation import validate_bars
from full_python.events import EventType
from full_python.instruments import instrument_for_point_value, instrument_spec
from full_python.reporting.html_report import render_html_report
from full_python.reporting.bootstrap import build_block_bootstrap_report
from full_python.reporting.survivability import (
    TradeResult,
    build_daily_metrics,
    build_monthly_breakdown,
    build_survivability_report,
)
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import AdaptiveTrendConfig, production_am_config
from full_python.strategy.baseline import BaselineMomentumStrategy
from full_python.strategy.config import BaselineMomentumConfig
from full_python.strategy.vwap_reversion import VwapReversionStrategy
from full_python.strategy.vwap_reversion_config import VwapReversionConfig
from full_python.strategy.opening_range_fade import OpeningRangeFadeStrategy
from full_python.strategy.opening_range_fade_config import OpeningRangeFadeConfig

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


def build_strategy(strategy_name: str, *, dollar_point_value: float | None = None):
    if strategy_name == "baseline":
        config = BaselineMomentumConfig()
        return config, BaselineMomentumStrategy(config)
    if strategy_name == "adaptive_trend":
        config = AdaptiveTrendConfig()
        if dollar_point_value is not None:
            config = replace(config, dollar_point_value=dollar_point_value)
        return config, AdaptiveTrendStrategy(config)
    if strategy_name == "adaptive_trend_am":
        config = production_am_config()
        if dollar_point_value is not None:
            config = replace(config, dollar_point_value=dollar_point_value)
        return config, AdaptiveTrendStrategy(config)
    if strategy_name == "vwap_reversion":
        config = VwapReversionConfig()
        return config, VwapReversionStrategy(config)
    if strategy_name == "opening_range_fade":
        config = OpeningRangeFadeConfig()
        return config, OpeningRangeFadeStrategy(config)
    raise ValueError(f"Unknown strategy: {strategy_name}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_commit(repo_root: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root or _repo_root(),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "0" * 40


def _source_tree_hash(repo_root: Path | None = None) -> str:
    """Hash executable source bytes, including dirty and untracked code."""
    root = repo_root or _repo_root()
    candidates = [root / "pyproject.toml"]
    for directory in (root / "src", root / "scripts"):
        if directory.exists():
            candidates.extend(directory.rglob("*.py"))
    digest = hashlib.sha256()
    for path in sorted((p for p in candidates if p.is_file()), key=lambda p: str(p)):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _source_is_dirty(repo_root: Path | None = None) -> bool:
    root = repo_root or _repo_root()
    try:
        result = subprocess.run(
            [
                "git", "status", "--porcelain", "--untracked-files=all", "--",
                "src", "scripts", "pyproject.toml",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return True


def _code_version_hash(repo_root: Path | None = None) -> str:
    """Content identity of the code that actually executed the run."""
    return _source_tree_hash(repo_root)


def run_baseline(
    *,
    data_path: str | Path,
    output_dir: str | Path,
    fill_timing: str = "next_bar_open",
    allow_dirty_data: bool = False,
    strategy_name: str = "baseline",
    simulation_overrides: dict | None = None,
    execution_instrument: str | None = None,
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
    overrides = dict(simulation_overrides or {})
    if execution_instrument is not None:
        spec = instrument_spec(execution_instrument)
        if "point_value" in overrides and overrides["point_value"] != spec.dollar_point_value:
            raise ValueError("point_value conflicts with execution_instrument")
        overrides.setdefault("point_value", spec.dollar_point_value)
        overrides.setdefault(
            "commission_per_contract_round_trip",
            spec.commission_per_contract_round_trip,
        )
    provisional_config = production_am_config() if strategy_name == "adaptive_trend_am" else None
    if getattr(provisional_config, "enable_daily_loss_limit", False):
        overrides.setdefault("daily_loss_limit", provisional_config.daily_loss_limit)
    simulation_config = SimulationConfig(fill_timing=fill_timing, **overrides)
    execution_spec = (
        instrument_spec(execution_instrument)
        if execution_instrument is not None
        else instrument_for_point_value(simulation_config.point_value)
    )
    strategy_config, strategy = build_strategy(
        strategy_name,
        dollar_point_value=simulation_config.point_value,
    )
    result = SimulationEngine(simulation_config).run(bars, strategy)

    # Deterministic run identity: same data + same configs + same code => same run id.
    repo_root = _repo_root()
    code_version = _code_version_hash(repo_root)
    git_commit = _git_commit(repo_root)
    source_dirty = _source_is_dirty(repo_root)
    run_id = "-".join(
        [
            manifest.stable_hash()[:8],
            strategy_config.parameter_hash()[:8],
            simulation_config.parameter_hash()[:8],
            code_version[:8],
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
    daily_rows: list[tuple[str, float, float]] = []
    with daily_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["session_date", "net_pnl", "cumulative_pnl"])
        for day in result.session_dates:
            pnl = daily_pnl.get(day, 0.0)
            cumulative += pnl
            daily_rows.append((day, pnl, cumulative))
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
    daily_series = [daily_pnl.get(day, 0.0) for day in result.session_dates]
    bootstrap = build_block_bootstrap_report(daily_series)
    monthly = build_monthly_breakdown(daily_pnl)
    exit_reasons: dict[str, int] = {}
    ambiguous_exits = 0
    for trade in result.trades:
        exit_reasons[trade.exit_reason] = exit_reasons.get(trade.exit_reason, 0) + 1
        if trade.ambiguous_exit:
            ambiguous_exits += 1

    report = {
        "run_id": run_id,
        "code_version": code_version,
        "source_provenance": {
            "git_commit": git_commit,
            "source_tree_sha256": code_version,
            "dirty": source_dirty,
        },
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
        "execution_instrument": asdict(execution_spec),
        "events_path": str(events_path),
        "trades_path": str(trades_path),
        "daily_pnl_path": str(daily_path),
        "survivability": survivability.to_dict(),
        "daily": daily_metrics.to_dict(),
        "bootstrap": bootstrap.to_dict(),
        "monthly": monthly,
        "exit_reasons": exit_reasons,
        "ambiguous_exits": ambiguous_exits,
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rejections: dict[str, int] = {}
    for record in result.ledger.records:
        if record.event_type == EventType.REJECTION:
            reason = str(record.payload.get("reason", "unknown"))
            rejections[reason] = rejections.get(reason, 0) + 1
    html_path = run_dir / "report.html"
    html_path.write_text(
        render_html_report(report, result.trades, daily_rows, rejections),
        encoding="utf-8",
    )
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
    parser.add_argument(
        "--strategy",
        default="baseline",
        choices=["baseline", "adaptive_trend", "adaptive_trend_am", "vwap_reversion", "opening_range_fade"],
        help="adaptive_trend = flat parity core; adaptive_trend_am = production sizing; vwap_reversion = MR variant 1 (v0.2); opening_range_fade = MR variant 2 (v1)",
    )
    parser.add_argument("--point-value", type=float, help="Override contract point value (default 2.0 = MNQ)")
    parser.add_argument(
        "--instrument",
        choices=["NQ", "MNQ"],
        help="Execution/risk instrument; market data may still be NQ-derived",
    )
    parser.add_argument("--commission-rt", type=float, help="Override round-trip commission per contract")
    parser.add_argument("--entry-slippage-points", type=float, help="Override entry slippage (e.g. 0.75 to mirror a TV run)")
    parser.add_argument("--exit-slippage-points", type=float, help="Override exit slippage")
    parser.add_argument("--rth-open-extra-slippage-points", type=float, help="Override the 9:30-9:45 extra entry slippage")
    args = parser.parse_args()
    overrides = {}
    if args.point_value is not None:
        overrides["point_value"] = args.point_value
    if args.commission_rt is not None:
        overrides["commission_per_contract_round_trip"] = args.commission_rt
    if args.entry_slippage_points is not None:
        overrides["entry_slippage_points"] = args.entry_slippage_points
    if args.exit_slippage_points is not None:
        overrides["exit_slippage_points"] = args.exit_slippage_points
    if args.rth_open_extra_slippage_points is not None:
        overrides["rth_open_extra_entry_slippage_points"] = args.rth_open_extra_slippage_points
    report_path = run_baseline(
        data_path=args.data,
        output_dir=args.output_dir,
        fill_timing=args.fill_timing,
        allow_dirty_data=args.allow_dirty_data,
        strategy_name=args.strategy,
        simulation_overrides=overrides,
        execution_instrument=args.instrument,
    )
    print(report_path)


if __name__ == "__main__":
    main()
