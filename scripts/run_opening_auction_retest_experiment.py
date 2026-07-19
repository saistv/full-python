"""Run the preregistered train-only Opening Auction Level-Retest v2 T1.

The loader constructs no bar at or after the 2025 confirmation boundary.  T1 is
registered before the first strategy decision and cannot overwrite an earlier run.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, time, timedelta, timezone
import gc
import hashlib
import json
import math
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
from full_python.research.opening_auction_retest import (
    build_opening_auction_retest_report,
    evaluate_retest_t1_primary_gates,
)
from full_python.research.registry import ExperimentRegistry, ExperimentSpec, TrialRecord
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.opening_auction_retest import OpeningAuctionRetestStrategy
from full_python.strategy.opening_auction_retest_config import (
    OpeningAuctionRetestConfig,
)


EXPERIMENT_ID = "oar-retest-v2-20260717"
SCORE_START_SESSION = "2021-03-16"
TRAIN_END_SESSION_EXCLUSIVE = "2025-01-01"
CANDIDATE_FAMILY_TRIAL_BUDGET = 9
EASTERN = ZoneInfo("America/New_York")


def _session_boundary_utc(session_date_exclusive: str) -> str:
    session_day = date.fromisoformat(session_date_exclusive)
    local = datetime.combine(
        session_day - timedelta(days=1), time(18, 0), tzinfo=EASTERN
    )
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_train_bars(
    path: str | Path, *, end_session_exclusive: str
) -> tuple[list[MarketBar], str]:
    """Load only train bars while validating the full timestamp sequence.

    Rows at and after the boundary are inspected only for timestamp ordering; their
    prices and volumes are never parsed into `MarketBar` objects or supplied to the
    strategy.  This catches a future row followed by a hidden earlier row instead
    of silently stopping at the first lexical boundary crossing.
    """
    boundary = _session_boundary_utc(end_session_exclusive)
    input_path = Path(path)
    bars: list[MarketBar] = []
    digest = hashlib.sha256()
    previous_timestamp: str | None = None
    crossed_boundary = False
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("input CSV does not satisfy the canonical bar schema")
        for row_number, row in enumerate(reader, start=2):
            timestamp = row["timestamp"]
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError(
                    "input timestamps are not strictly increasing at "
                    f"row {row_number}: {timestamp} <= {previous_timestamp}"
                )
            previous_timestamp = timestamp
            if timestamp >= boundary:
                crossed_boundary = True
                continue
            if crossed_boundary:
                raise ValueError("eligible train row appeared after the excluded boundary")
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
            for key in ("opening_minutes", "opening_closes"):
                encoded[key] = json.dumps(encoded[key], separators=(",", ":"))
            writer.writerow(encoded)


def _write_diagnostics(path: Path, events) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "session_date",
            "timestamp_utc",
            "event",
            "regime",
            "side",
            "state",
            "metadata_json",
        ]
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
                    "state": row["state"],
                    "metadata_json": json.dumps(
                        row["metadata"], sort_keys=True, separators=(",", ":")
                    ),
                }
            )


def _stable_payload_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sequence_hash(payloads) -> str:
    digest = hashlib.sha256()
    for payload in payloads:
        digest.update(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _core_replay_hashes(result, strategy, research: dict) -> dict[str, str]:
    return {
        "ledger": _sequence_hash(record.to_dict() for record in result.ledger.records),
        "trades": _sequence_hash(trade.to_payload() for trade in result.trades),
        "snapshots": _sequence_hash(
            snapshot.to_dict() for snapshot in strategy.session_diagnostics
        ),
        "diagnostics": _sequence_hash(
            event.to_dict() for event in strategy.diagnostic_events
        ),
        "research_core": _stable_payload_hash(research),
    }


def run(
    *,
    data_path: Path,
    output_dir: Path,
    registry_path: Path,
    allocated_capital: float | None = None,
    hard_loss_limit: float | None = None,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing output directory: {output_dir}")
    if registry_path.exists():
        raise FileExistsError(f"refusing to reuse existing experiment registry: {registry_path}")
    if (allocated_capital is None) != (hard_loss_limit is None):
        raise ValueError("allocated_capital and hard_loss_limit must be supplied together")
    if allocated_capital is not None and (
        not math.isfinite(allocated_capital) or allocated_capital <= 0
    ):
        raise ValueError("allocated_capital must be finite and positive")
    if hard_loss_limit is not None and (
        not math.isfinite(hard_loss_limit) or hard_loss_limit <= 0
    ):
        raise ValueError("hard_loss_limit must be finite and positive")

    strategy_config = OpeningAuctionRetestConfig()
    simulation_config = SimulationConfig(
        point_value=20.0,
        commission_per_contract_round_trip=10.0,
        entry_slippage_points=0.75,
        exit_slippage_points=0.75,
        rth_open_extra_entry_slippage_points=0.0,
        fill_timing="next_bar_open",
        daily_loss_limit=None,
    )
    evaluation_policy = {
        "score_start_session": SCORE_START_SESSION,
        "score_end_session_exclusive": TRAIN_END_SESSION_EXCLUSIVE,
        "candidate_family_trial_budget": CANDIDATE_FAMILY_TRIAL_BUDGET,
        "bootstrap_block_length_sessions": 10,
        "bootstrap_draws": 20_000,
        "bootstrap_seed": 20260712,
        "allocated_capital": allocated_capital,
        "hard_loss_limit": hard_loss_limit,
        "deterministic_replays": 2,
    }
    evaluation_policy_hash = _stable_payload_hash(evaluation_policy)
    bars, train_sequence_hash = load_train_bars(
        data_path, end_session_exclusive=TRAIN_END_SESSION_EXCLUSIVE
    )
    quality = validate_bars(bars)
    if not quality.is_structurally_clean:
        raise ValueError(
            "train data failed structural validation: "
            f"{quality.structural_issue_count} issues {quality.issue_counts}"
        )

    root = Path(__file__).resolve().parents[1]
    hypothesis_path = root / "docs/research/2026-07-17-opening-auction-retest-v2-hypothesis.md"
    standard_path = root / "docs/specs/2026-07-17-automation-worthiness-standard.md"
    hypothesis_hash = file_sha256(hypothesis_path)
    standard_hash = file_sha256(standard_path)
    code_hash = _code_version_hash(root)
    full_file_hash = file_sha256(data_path)
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
        objective=(
            "Test whether the first controlled retest of an accepted or rejected "
            "opening-auction external level has positive post-cost expectancy"
        ),
        hypothesis=(
            "Acceptance or rejection of overnight/prior-RTH extremes is context; "
            "only the first post-opening level hold plus later confirmation is tradable"
        ),
        data_hash=data_hash,
        strategy_hash=strategy_config.parameter_hash(),
        simulation_hash=simulation_config.parameter_hash(),
        code_hash=code_hash,
        trial_budget=CANDIDATE_FAMILY_TRIAL_BUDGET,
        parent_experiment_id=None,
        notes=(
            f"T1 frozen; hypothesis_sha256={hypothesis_hash}; "
            f"automation_standard_sha256={standard_hash}; "
            f"evaluation_policy_sha256={evaluation_policy_hash}; "
            "T2-T9 forbidden unless T1 normal-cost primary gates pass"
        ),
    )

    output_dir.mkdir(parents=True)
    events_path = output_dir / "events.jsonl"
    trades_path = output_dir / "trades.csv"
    snapshots_path = output_dir / "auction_sessions.csv"
    diagnostics_path = output_dir / "auction_diagnostics.csv"
    with ExperimentRegistry(registry_path) as registry:
        # Registration is committed before the first bar reaches the strategy.
        registry.register(spec)
        strategy = OpeningAuctionRetestStrategy(strategy_config)
        result = SimulationEngine(simulation_config).run(bars, strategy)
        research = build_opening_auction_retest_report(
            trades=result.trades,
            ledger=result.ledger,
            snapshots=strategy.session_diagnostics,
            diagnostic_events=strategy.diagnostic_events,
            point_value=simulation_config.point_value,
            score_start_session=SCORE_START_SESSION,
            score_end_session_exclusive=TRAIN_END_SESSION_EXCLUSIVE,
            candidate_family_trial_budget=CANDIDATE_FAMILY_TRIAL_BUDGET,
            expected_entry_delay_bars=simulation_config.entry_delay_bars,
            allocated_capital=allocated_capital,
            hard_loss_limit=hard_loss_limit,
        )
        first_core_hashes = _core_replay_hashes(result, strategy, research)
        result.ledger.write_jsonl(events_path)
        _write_trades(trades_path, result.trades)
        _write_snapshots(snapshots_path, strategy.session_diagnostics)
        _write_diagnostics(diagnostics_path, strategy.diagnostic_events)
        file_artifact_hashes = {
            "events_jsonl_sha256": file_sha256(events_path),
            "trades_csv_sha256": file_sha256(trades_path),
            "auction_sessions_csv_sha256": file_sha256(snapshots_path),
            "auction_diagnostics_csv_sha256": file_sha256(diagnostics_path),
        }
        del result
        del strategy
        gc.collect()

        verification_strategy = OpeningAuctionRetestStrategy(strategy_config)
        verification_result = SimulationEngine(simulation_config).run(
            bars, verification_strategy
        )
        verification_research = build_opening_auction_retest_report(
            trades=verification_result.trades,
            ledger=verification_result.ledger,
            snapshots=verification_strategy.session_diagnostics,
            diagnostic_events=verification_strategy.diagnostic_events,
            point_value=simulation_config.point_value,
            score_start_session=SCORE_START_SESSION,
            score_end_session_exclusive=TRAIN_END_SESSION_EXCLUSIVE,
            candidate_family_trial_budget=CANDIDATE_FAMILY_TRIAL_BUDGET,
            expected_entry_delay_bars=simulation_config.entry_delay_bars,
            allocated_capital=allocated_capital,
            hard_loss_limit=hard_loss_limit,
        )
        second_core_hashes = _core_replay_hashes(
            verification_result, verification_strategy, verification_research
        )
        deterministic_mismatches = sorted(
            key
            for key in first_core_hashes
            if first_core_hashes[key] != second_core_hashes.get(key)
        )
        del verification_result
        del verification_strategy
        del verification_research
        gc.collect()

        research["deterministic_replay"] = {
            "verified": not deterministic_mismatches,
            "core_hashes": first_core_hashes,
            "mismatches": deterministic_mismatches,
        }
        research["t1_primary_gates"] = evaluate_retest_t1_primary_gates(research)
        research["promotion_status"] = (
            "historical_primary_only_not_research_worthy"
            if research["t1_primary_gates"]["passed"]
            else "rejected_primary_no_threshold_rescue"
        )
        artifact_hashes = {
            **file_artifact_hashes,
            **{f"canonical_{key}_sha256": value for key, value in first_core_hashes.items()},
            "research_result_sha256": _stable_payload_hash(research),
            "evaluation_policy_sha256": evaluation_policy_hash,
        }
        registry.record_trial(
            TrialRecord(
                experiment_id=EXPERIMENT_ID,
                trial_index=1,
                config_hash=strategy_config.parameter_hash(),
                overrides={
                    "evaluation_policy": evaluation_policy,
                    "evaluation_policy_sha256": evaluation_policy_hash,
                },
                metrics={
                    "overall": research["overall"],
                    "by_branch": research["by_branch"],
                    "by_side": research["by_side"],
                    "weekly": research["weekly"],
                    "statistical_confidence": research["statistical_confidence"],
                    "t1_primary_gates": research["t1_primary_gates"],
                    "artifact_hashes": artifact_hashes,
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

    report = {
        "experiment_id": EXPERIMENT_ID,
        "trial_index": 1,
        "stage": "historical_train",
        "historical_evidence_is_pristine": False,
        "prospective_validation_required": True,
        "source_provenance": {
            "git_commit": _git_commit(root),
            "source_tree_sha256": code_hash,
            "dirty": _source_is_dirty(root),
            "hypothesis_sha256": hypothesis_hash,
            "automation_standard_sha256": standard_hash,
            "evaluation_policy_sha256": evaluation_policy_hash,
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
        "evaluation_policy": evaluation_policy,
        "artifacts": {
            "events": {
                "path": str(events_path),
                "sha256": artifact_hashes["events_jsonl_sha256"],
            },
            "trades": {
                "path": str(trades_path),
                "sha256": artifact_hashes["trades_csv_sha256"],
            },
            "auction_sessions": {
                "path": str(snapshots_path),
                "sha256": artifact_hashes["auction_sessions_csv_sha256"],
            },
            "auction_diagnostics": {
                "path": str(diagnostics_path),
                "sha256": artifact_hashes["auction_diagnostics_csv_sha256"],
            },
            "registry": str(registry_path),
            "canonical_hashes": artifact_hashes,
        },
        "research": research,
        "decision": (
            "primary_pass_run_only_preregistered_robustness_and_stress_trials"
            if research["t1_primary_gates"]["passed"]
            else "reject_v2_without_threshold_or_branch_salvage"
        ),
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run preregistered Opening Auction Level-Retest v2 train T1"
    )
    parser.add_argument(
        "--data", default="runs/multi-year/nq1_2021-03-16_2026-06-26.csv"
    )
    parser.add_argument(
        "--output-dir", default="runs/opening-auction-retest-v2/train-t1"
    )
    parser.add_argument(
        "--registry", default="runs/opening-auction-retest-v2/experiments.sqlite"
    )
    parser.add_argument("--allocated-capital", type=float)
    parser.add_argument("--hard-loss-limit", type=float)
    args = parser.parse_args()
    print(
        run(
            data_path=Path(args.data),
            output_dir=Path(args.output_dir),
            registry_path=Path(args.registry),
            allocated_capital=args.allocated_capital,
            hard_loss_limit=args.hard_loss_limit,
        )
    )


if __name__ == "__main__":
    main()
