"""Run the preregistered, train-only Opening Auction Regime v1 trial.

This runner physically stops reading before the first bar belonging to the
historical confirmation window. It is intentionally separate from the normal
all-history CLI so T1 cannot accidentally consume 2025-2026 outcomes.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from full_python.cli import (
    TRADE_CSV_COLUMNS,
    _code_version_hash,
    _git_commit,
    _source_is_dirty,
)
from full_python.data.manifest import file_sha256
from full_python.data.validation import validate_bars
from full_python.models import MarketBar
from full_python.research.opening_auction import build_opening_auction_report
from full_python.research.registry import ExperimentRegistry, ExperimentSpec, TrialRecord
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.opening_auction_regime import OpeningAuctionRegimeStrategy
from full_python.strategy.opening_auction_regime_config import OpeningAuctionRegimeConfig


EXPERIMENT_ID = "oar-v1-20260717"
SCORE_START_SESSION = "2021-03-16"
TRAIN_END_SESSION_EXCLUSIVE = "2025-01-01"
EASTERN = ZoneInfo("America/New_York")


def _session_boundary_utc(session_date_exclusive: str) -> str:
    """UTC timestamp at the 18:00 ET start of the excluded CME session."""
    session_day = date.fromisoformat(session_date_exclusive)
    local = datetime.combine(
        session_day - timedelta(days=1), time(18, 0), tzinfo=EASTERN
    )
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_bars_before_session(
    path: str | Path, *, end_session_exclusive: str
) -> tuple[list[MarketBar], str]:
    """Load a sorted canonical CSV without parsing any excluded-session bar."""
    boundary = _session_boundary_utc(end_session_exclusive)
    bars: list[MarketBar] = []
    digest = hashlib.sha256()
    input_path = Path(path)
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("input CSV does not satisfy the canonical bar schema")
        for row in reader:
            timestamp = row["timestamp"]
            if timestamp >= boundary:
                break
            bar = MarketBar(
                timestamp_utc=timestamp,
                symbol=row["symbol"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            bars.append(bar)
            digest.update(
                json.dumps(
                    {"timestamp_utc": timestamp, **bar.to_payload()},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            digest.update(b"\n")
    if not bars:
        raise ValueError("no bars precede the train boundary")
    if bars[-1].timestamp_utc >= boundary:
        raise AssertionError("train loader crossed its physical boundary")
    return bars, digest.hexdigest()


def _write_trades(path: Path, trades) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_CSV_COLUMNS)
        writer.writeheader()
        for trade in trades:
            writer.writerow(trade.to_payload())


def _write_snapshots(path: Path, snapshots) -> None:
    rows = [snapshot.to_dict() for snapshot in snapshots]
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            encoded = dict(row)
            for key in ("opening_minutes", "last_vwap_sides"):
                encoded[key] = json.dumps(encoded[key], separators=(",", ":"))
            writer.writerow(encoded)


def _write_diagnostics(path: Path, events) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["session_date", "timestamp_utc", "event", "regime", "side", "metadata_json"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for event in events:
            row = event.to_dict()
            writer.writerow(
                {
                    "session_date": row["session_date"],
                    "timestamp_utc": row["timestamp_utc"],
                    "event": row["event"],
                    "regime": row["regime"],
                    "side": row["side"],
                    "metadata_json": json.dumps(
                        row["metadata"], sort_keys=True, separators=(",", ":")
                    ),
                }
            )


def run(
    *,
    data_path: Path,
    output_dir: Path,
    registry_path: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    strategy_config = OpeningAuctionRegimeConfig()
    simulation_config = SimulationConfig(
        point_value=20.0,
        commission_per_contract_round_trip=10.0,
        entry_slippage_points=0.75,
        exit_slippage_points=0.75,
        rth_open_extra_entry_slippage_points=0.0,
        fill_timing="next_bar_open",
        daily_loss_limit=None,
    )
    bars, train_sequence_hash = load_bars_before_session(
        data_path, end_session_exclusive=TRAIN_END_SESSION_EXCLUSIVE
    )
    quality = validate_bars(bars)
    if not quality.is_structurally_clean:
        raise ValueError(
            "train data failed structural validation: "
            f"{quality.structural_issue_count} issues {quality.issue_counts}"
        )

    code_hash = _code_version_hash()
    full_file_hash = file_sha256(data_path)
    hypothesis_path = (
        Path(__file__).resolve().parents[1]
        / "docs/research/2026-07-17-opening-auction-regime-v1-hypothesis.md"
    )
    hypothesis_hash = file_sha256(hypothesis_path)
    data_hash = hashlib.sha256(
        "|".join(
            (
                full_file_hash,
                train_sequence_hash,
                SCORE_START_SESSION,
                TRAIN_END_SESSION_EXCLUSIVE,
                str(len(bars)),
                bars[-1].timestamp_utc,
            )
        ).encode("utf-8")
    ).hexdigest()
    spec = ExperimentSpec(
        experiment_id=EXPERIMENT_ID,
        objective="Test a causal opening-auction state machine on historical train only",
        hypothesis=(
            "Initiative acceptance followed by pullback/re-acceleration, and strongly "
            "confirmed failed external auctions, have positive post-cost expectancy"
        ),
        data_hash=data_hash,
        strategy_hash=strategy_config.parameter_hash(),
        simulation_hash=simulation_config.parameter_hash(),
        code_hash=code_hash,
        trial_budget=11,
        notes=(
            f"T1 frozen default; hypothesis_sha256={hypothesis_hash}; "
            "T2-T11 forbidden unless T1 primary gates pass"
        ),
    )

    # Registration precedes the first strategy decision.
    with ExperimentRegistry(registry_path) as registry:
        registry.register(spec)
        strategy = OpeningAuctionRegimeStrategy(strategy_config)
        result = SimulationEngine(simulation_config).run(bars, strategy)
        research = build_opening_auction_report(
            trades=result.trades,
            ledger=result.ledger,
            snapshots=strategy.session_diagnostics,
            diagnostic_events=strategy.diagnostic_events,
            point_value=simulation_config.point_value,
            score_start_session=SCORE_START_SESSION,
            score_end_session_exclusive=TRAIN_END_SESSION_EXCLUSIVE,
        )
        registry.record_trial(
            TrialRecord(
                experiment_id=EXPERIMENT_ID,
                trial_index=1,
                config_hash=strategy_config.parameter_hash(),
                overrides={},
                metrics={
                    "overall": research["overall"],
                    "by_branch": research["by_branch"],
                    "by_side": research["by_side"],
                    "classifier_counts": research["classifier_counts"],
                    "t1_primary_gates": research["t1_primary_gates"],
                },
                status=(
                    "passed_primary"
                    if research["t1_primary_gates"]["passed"]
                    else "rejected_primary"
                ),
            )
        )
        if not research["t1_primary_gates"]["passed"]:
            registry.complete(EXPERIMENT_ID)

    events_path = output_dir / "events.jsonl"
    trades_path = output_dir / "trades.csv"
    snapshots_path = output_dir / "auction_sessions.csv"
    diagnostics_path = output_dir / "auction_diagnostics.csv"
    result.ledger.write_jsonl(events_path)
    _write_trades(trades_path, result.trades)
    _write_snapshots(snapshots_path, strategy.session_diagnostics)
    _write_diagnostics(diagnostics_path, strategy.diagnostic_events)

    report = {
        "experiment_id": EXPERIMENT_ID,
        "trial_index": 1,
        "stage": "historical_train",
        "historical_evidence_is_pristine": False,
        "prospective_validation_required": True,
        "source_provenance": {
            "git_commit": _git_commit(),
            "source_tree_sha256": code_hash,
            "dirty": _source_is_dirty(),
            "hypothesis_sha256": hypothesis_hash,
        },
        "data": {
            "path": str(data_path),
            "full_file_sha256": full_file_hash,
            "train_sequence_sha256": train_sequence_hash,
            "registered_data_hash": data_hash,
            "bar_count": len(bars),
            "first_timestamp_utc": bars[0].timestamp_utc,
            "last_timestamp_utc": bars[-1].timestamp_utc,
            "excluded_session_start_utc": _session_boundary_utc(
                TRAIN_END_SESSION_EXCLUSIVE
            ),
            "quality": quality.to_dict(),
        },
        "strategy": {
            **strategy_config.to_dict(),
            "parameter_hash": strategy_config.parameter_hash(),
        },
        "simulation": {
            **simulation_config.to_dict(),
            "parameter_hash": simulation_config.parameter_hash(),
        },
        "artifacts": {
            "events": str(events_path),
            "trades": str(trades_path),
            "auction_sessions": str(snapshots_path),
            "auction_diagnostics": str(diagnostics_path),
            "registry": str(registry_path),
        },
        "research": research,
        "decision": (
            "proceed_to_registered_robustness_trials"
            if research["t1_primary_gates"]["passed"]
            else "reject_v1_without_threshold_salvage"
        ),
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run preregistered Opening Auction Regime v1 train trial"
    )
    parser.add_argument(
        "--data", default="runs/multi-year/nq1_2021-03-16_2026-06-26.csv"
    )
    parser.add_argument(
        "--output-dir", default="runs/opening-auction-regime-v1/train-t1"
    )
    parser.add_argument(
        "--registry", default="runs/opening-auction-regime-v1/experiments.sqlite"
    )
    args = parser.parse_args()
    print(
        run(
            data_path=Path(args.data),
            output_dir=Path(args.output_dir),
            registry_path=Path(args.registry),
        )
    )


if __name__ == "__main__":
    main()
