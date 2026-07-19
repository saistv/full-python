"""Run the preregistered train-only Overnight Displacement Reversal v3 T1.

This is the candidate's only historical composition root.  It intentionally is
not registered in ``full_python.cli``.  The bounded loader constructs no market
bar at or after the 2025 confirmation boundary, T1 is committed to an insert-only
registry before the first strategy bar, and a second fresh replay must reproduce
the complete canonical research core.
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
import re
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from full_python.data.manifest import file_sha256
from full_python.data.validation import validate_bars
from full_python.models import MarketBar, Trade
from full_python.research.overnight_displacement_reversal import (
    build_overnight_displacement_reversal_report,
    evaluate_odr_t1_primary_gates,
)
from full_python.research.registry import ExperimentRegistry, ExperimentSpec, TrialRecord
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.overnight_displacement_reversal import (
    OvernightDisplacementReversalStrategy,
)
from full_python.strategy.overnight_displacement_reversal_config import (
    OvernightDisplacementReversalConfig,
)


EXPERIMENT_ID = "overnight-displacement-reversal-v3-20260718"
SCORE_START_SESSION = "2021-03-16"
TRAIN_END_SESSION_EXCLUSIVE = "2025-01-01"
CANDIDATE_FAMILY_TRIAL_BUDGET = 9
MAX_WARMUP_EXPECTED_SESSIONS = 25
EASTERN = ZoneInfo("America/New_York")
CANONICAL_UTC_MINUTE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:00Z$"
)
TRADE_CSV_COLUMNS = tuple(Trade.__dataclass_fields__)


def repository_root() -> Path:
    """Return the project root without importing the general-purpose CLI."""
    return Path(__file__).resolve().parents[1]


def git_commit(repo_root: Path) -> str:
    """Return the current commit, or an explicit unavailable marker."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unavailable"
    return result.stdout.strip() or "unavailable"


def source_is_dirty(repo_root: Path) -> bool:
    """Disclose changes to executable inputs while preserving their byte hash."""
    try:
        result = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                "src",
                "scripts",
                "pyproject.toml",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return True
    return bool(result.stdout.strip())


def source_tree_sha256(repo_root: Path) -> str:
    """Hash every executable Python source plus the project definition."""
    candidates = [repo_root / "pyproject.toml"]
    for directory in (repo_root / "src", repo_root / "scripts"):
        if directory.exists():
            candidates.extend(directory.rglob("*.py"))
    digest = hashlib.sha256()
    for path in sorted(
        (candidate for candidate in candidates if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(repo_root).as_posix(),
    ):
        relative = path.relative_to(repo_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def stable_payload_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def sequence_sha256(payloads: Iterable[object]) -> str:
    digest = hashlib.sha256()
    for payload in payloads:
        digest.update(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def parse_canonical_utc_minute(raw: str) -> datetime:
    """Parse only exact, minute-aligned ``YYYY-MM-DDTHH:MM:00Z`` timestamps."""
    if not isinstance(raw, str) or CANONICAL_UTC_MINUTE.fullmatch(raw) is None:
        raise ValueError(
            "timestamp must be canonical minute-aligned UTC "
            f"(YYYY-MM-DDTHH:MM:00Z): {raw!r}"
        )
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:00Z").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ValueError(f"invalid canonical UTC timestamp: {raw!r}") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:00Z") != raw:
        raise ValueError(f"timestamp does not round-trip canonically: {raw!r}")
    return parsed


def session_boundary_utc(session_date_exclusive: str) -> datetime:
    """Return the 18:00 ET start of the excluded CME trading session."""
    session_day = date.fromisoformat(session_date_exclusive)
    local = datetime.combine(
        session_day - timedelta(days=1), time(18, 0), tzinfo=EASTERN
    )
    return local.astimezone(timezone.utc)


def session_boundary_text(session_date_exclusive: str) -> str:
    return session_boundary_utc(session_date_exclusive).strftime(
        "%Y-%m-%dT%H:%M:00Z"
    )


def load_bounded_train_bars(
    path: str | Path, *, end_session_exclusive: str
) -> tuple[list[MarketBar], str]:
    """Load only train prices while validating every row's timestamp sequence.

    Rows at and after the boundary are inspected only for their timestamp.  Their
    symbol, OHLC, and volume cells are never accessed or parsed, so confirmation
    prices cannot reach the candidate even if they coexist in the source file.
    """
    boundary = session_boundary_utc(end_session_exclusive)
    input_path = Path(path)
    bars: list[MarketBar] = []
    digest = hashlib.sha256()
    previous_timestamp: datetime | None = None
    crossed_boundary = False
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("input CSV does not satisfy the canonical bar schema")
        for row_number, row in enumerate(reader, start=2):
            raw_timestamp = row["timestamp"]
            try:
                timestamp = parse_canonical_utc_minute(raw_timestamp)
            except ValueError as error:
                raise ValueError(f"invalid timestamp at row {row_number}: {error}") from error
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError(
                    "input timestamps are not strictly increasing at "
                    f"row {row_number}: {raw_timestamp} <= "
                    f"{previous_timestamp.strftime('%Y-%m-%dT%H:%M:00Z')}"
                )
            previous_timestamp = timestamp
            if timestamp >= boundary:
                crossed_boundary = True
                continue
            if crossed_boundary:
                raise ValueError("eligible train row appeared after the excluded boundary")

            symbol = row["symbol"]
            if not symbol:
                raise ValueError(f"empty symbol at row {row_number}")
            try:
                bar = MarketBar(
                    timestamp_utc=raw_timestamp,
                    symbol=symbol,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid train OHLCV at row {row_number}") from error
            bars.append(bar)
            digest.update(
                json.dumps(
                    {"timestamp_utc": raw_timestamp, **bar.to_payload()},
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
            digest.update(b"\n")
    if not bars:
        raise ValueError("no bars precede the train boundary")
    if parse_canonical_utc_minute(bars[-1].timestamp_utc) >= boundary:
        raise AssertionError("bounded train loader crossed its physical boundary")
    return bars, digest.hexdigest()


def _csv_cell(value: object) -> object:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    return value


def write_mapping_rows_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Write stable CSV columns and JSON-encode structured diagnostic cells."""
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_cell(row.get(key)) for key in fieldnames})


def write_trades_csv(path: Path, trades: Sequence[Trade]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_CSV_COLUMNS)
        writer.writeheader()
        for trade in trades:
            writer.writerow(trade.to_payload())


def core_replay_hashes(result, strategy, research: Mapping[str, Any]) -> dict[str, str]:
    """Canonical identities which two fresh replays must match exactly."""
    return {
        "ledger": sequence_sha256(record.to_dict() for record in result.ledger.records),
        "trades": sequence_sha256(trade.to_payload() for trade in result.trades),
        "session_dates": sequence_sha256(result.session_dates),
        "snapshots": sequence_sha256(
            snapshot.to_dict() for snapshot in strategy.session_diagnostics
        ),
        "diagnostics": sequence_sha256(
            event.to_dict() for event in strategy.diagnostic_events
        ),
        "research_core": stable_payload_sha256(research),
    }


def _validate_capital_policy(
    allocated_capital: float | None, hard_loss_limit: float | None
) -> None:
    if (allocated_capital is None) != (hard_loss_limit is None):
        raise ValueError("allocated_capital and hard_loss_limit must be supplied together")
    for name, value in (
        ("allocated_capital", allocated_capital),
        ("hard_loss_limit", hard_loss_limit),
    ):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be finite and positive")


def run(
    *,
    data_path: Path,
    output_dir: Path,
    registry_path: Path,
    allocated_capital: float | None = None,
    hard_loss_limit: float | None = None,
) -> Path:
    """Execute the one frozen normal-cost T1 and return its report path."""
    if output_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite existing output directory: {output_dir}"
        )
    if registry_path.exists():
        raise FileExistsError(
            f"refusing to reuse existing experiment registry: {registry_path}"
        )
    _validate_capital_policy(allocated_capital, hard_loss_limit)

    strategy_config = OvernightDisplacementReversalConfig()
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
        "maximum_warmup_expected_sessions": MAX_WARMUP_EXPECTED_SESSIONS,
        "candidate_family_trial_budget": CANDIDATE_FAMILY_TRIAL_BUDGET,
        "bootstrap_block_length_sessions": 10,
        "bootstrap_draws": 20_000,
        "bootstrap_seed": 20260712,
        "allocated_capital": allocated_capital,
        "hard_loss_limit": hard_loss_limit,
        "deterministic_replays": 2,
    }
    evaluation_policy_hash = stable_payload_sha256(evaluation_policy)
    bars, train_sequence_hash = load_bounded_train_bars(
        data_path, end_session_exclusive=TRAIN_END_SESSION_EXCLUSIVE
    )
    quality = validate_bars(bars)
    if not quality.is_structurally_clean:
        raise ValueError(
            "train data failed structural validation: "
            f"{quality.structural_issue_count} issues {quality.issue_counts}"
        )

    root = repository_root()
    hypothesis_path = (
        root
        / "docs/research/2026-07-18-overnight-displacement-reversal-v3-hypothesis.md"
    )
    standard_path = root / "docs/specs/2026-07-17-automation-worthiness-standard.md"
    hypothesis_hash = file_sha256(hypothesis_path)
    standard_hash = file_sha256(standard_path)
    code_hash = source_tree_sha256(root)
    full_file_hash = file_sha256(data_path)
    data_hash = stable_payload_sha256(
        {
            "full_file_sha256": full_file_hash,
            "train_sequence_sha256": train_sequence_hash,
            "score_start_session": SCORE_START_SESSION,
            "end_session_exclusive": TRAIN_END_SESSION_EXCLUSIVE,
            "bar_count": len(bars),
            "first_timestamp_utc": bars[0].timestamp_utc,
            "last_timestamp_utc": bars[-1].timestamp_utc,
        }
    )
    spec = ExperimentSpec(
        experiment_id=EXPERIMENT_ID,
        objective=(
            "Test whether a completed RTH extension and rejection of a causally "
            "displaced overnight session has positive post-cost expectancy"
        ),
        hypothesis=(
            "A gap with aligned overnight displacement breadth is tradable only "
            "after RTH extends the gap and decisively closes through its open"
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
            "T2-T9 forbidden unless every T1 normal-cost primary gate passes"
        ),
    )

    output_dir.mkdir(parents=True)
    events_path = output_dir / "events.jsonl"
    trades_path = output_dir / "trades.csv"
    sessions_path = output_dir / "displacement_sessions.csv"
    diagnostics_path = output_dir / "displacement_diagnostics.csv"
    report_path = output_dir / "report.json"

    with ExperimentRegistry(registry_path) as registry:
        # The commit happens before either strategy instance sees its first bar.
        registry.register(spec)

        strategy = OvernightDisplacementReversalStrategy(strategy_config)
        result = SimulationEngine(simulation_config).run(bars, strategy)
        research = build_overnight_displacement_reversal_report(
            trades=result.trades,
            ledger=result.ledger,
            snapshots=strategy.session_diagnostics,
            diagnostic_events=strategy.diagnostic_events,
            point_value=simulation_config.point_value,
            score_start_session=SCORE_START_SESSION,
            score_end_session_exclusive=TRAIN_END_SESSION_EXCLUSIVE,
            candidate_family_trial_budget=CANDIDATE_FAMILY_TRIAL_BUDGET,
            expected_entry_delay_bars=simulation_config.entry_delay_bars,
            commission_per_contract_round_trip=(
                simulation_config.commission_per_contract_round_trip
            ),
            entry_slippage_points=simulation_config.entry_slippage_points,
            exit_slippage_points=simulation_config.exit_slippage_points,
            tick_size=strategy_config.tick_size,
            allocated_capital=allocated_capital,
            hard_loss_limit=hard_loss_limit,
        )
        first_core_hashes = core_replay_hashes(result, strategy, research)
        result.ledger.write_jsonl(events_path)
        write_trades_csv(trades_path, result.trades)
        write_mapping_rows_csv(
            sessions_path,
            [snapshot.to_dict() for snapshot in strategy.session_diagnostics],
        )
        write_mapping_rows_csv(
            diagnostics_path,
            [event.to_dict() for event in strategy.diagnostic_events],
        )
        file_artifact_hashes = {
            "events_jsonl_sha256": file_sha256(events_path),
            "trades_csv_sha256": file_sha256(trades_path),
            "displacement_sessions_csv_sha256": file_sha256(sessions_path),
            "displacement_diagnostics_csv_sha256": file_sha256(diagnostics_path),
        }
        del result
        del strategy
        gc.collect()

        verification_strategy = OvernightDisplacementReversalStrategy(strategy_config)
        verification_result = SimulationEngine(simulation_config).run(
            bars, verification_strategy
        )
        verification_research = build_overnight_displacement_reversal_report(
            trades=verification_result.trades,
            ledger=verification_result.ledger,
            snapshots=verification_strategy.session_diagnostics,
            diagnostic_events=verification_strategy.diagnostic_events,
            point_value=simulation_config.point_value,
            score_start_session=SCORE_START_SESSION,
            score_end_session_exclusive=TRAIN_END_SESSION_EXCLUSIVE,
            candidate_family_trial_budget=CANDIDATE_FAMILY_TRIAL_BUDGET,
            expected_entry_delay_bars=simulation_config.entry_delay_bars,
            commission_per_contract_round_trip=(
                simulation_config.commission_per_contract_round_trip
            ),
            entry_slippage_points=simulation_config.entry_slippage_points,
            exit_slippage_points=simulation_config.exit_slippage_points,
            tick_size=strategy_config.tick_size,
            allocated_capital=allocated_capital,
            hard_loss_limit=hard_loss_limit,
        )
        second_core_hashes = core_replay_hashes(
            verification_result, verification_strategy, verification_research
        )
        deterministic_mismatches = sorted(
            key
            for key, first_hash in first_core_hashes.items()
            if second_core_hashes.get(key) != first_hash
        )
        del verification_result
        del verification_strategy
        del verification_research
        gc.collect()

        research["deterministic_replay"] = {
            "verified": not deterministic_mismatches,
            "core_hashes": first_core_hashes,
            "verification_core_hashes": second_core_hashes,
            "mismatches": deterministic_mismatches,
        }
        research["t1_primary_gates"] = evaluate_odr_t1_primary_gates(research)
        primary_passed = bool(research["t1_primary_gates"]["passed"])
        research["promotion_status"] = (
            "primary_qualified_robustness_capital_and_prospective_pending"
            if primary_passed
            else "rejected_primary_no_threshold_or_side_rescue"
        )

        artifact_hashes = {
            **file_artifact_hashes,
            **{
                f"canonical_{name}_sha256": value
                for name, value in first_core_hashes.items()
            },
            "research_result_sha256": stable_payload_sha256(research),
            "evaluation_policy_sha256": evaluation_policy_hash,
            "strategy_config_sha256": strategy_config.parameter_hash(),
            "simulation_config_sha256": simulation_config.parameter_hash(),
            "source_tree_sha256": code_hash,
            "hypothesis_sha256": hypothesis_hash,
            "automation_standard_sha256": standard_hash,
            "full_file_sha256": full_file_hash,
            "train_sequence_sha256": train_sequence_hash,
            "registered_data_sha256": data_hash,
        }
        report = {
            "experiment_id": EXPERIMENT_ID,
            "trial_index": 1,
            "stage": "historical_train",
            "historical_evidence_is_pristine": False,
            "prospective_validation_required": True,
            "source_provenance": {
                "git_commit": git_commit(root),
                "source_tree_sha256": code_hash,
                "dirty": source_is_dirty(root),
                "python_version": sys.version,
                "hypothesis_sha256": hypothesis_hash,
                "automation_standard_sha256": standard_hash,
                "evaluation_policy_sha256": evaluation_policy_hash,
            },
            "data": {
                "path": str(data_path),
                "full_file_sha256": full_file_hash,
                "train_sequence_sha256": train_sequence_hash,
                "registered_data_sha256": data_hash,
                "bar_count": len(bars),
                "first_timestamp_utc": bars[0].timestamp_utc,
                "last_timestamp_utc": bars[-1].timestamp_utc,
                "excluded_session_start_utc": session_boundary_text(
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
                    "sha256": file_artifact_hashes["events_jsonl_sha256"],
                },
                "trades": {
                    "path": str(trades_path),
                    "sha256": file_artifact_hashes["trades_csv_sha256"],
                },
                "displacement_sessions": {
                    "path": str(sessions_path),
                    "sha256": file_artifact_hashes[
                        "displacement_sessions_csv_sha256"
                    ],
                },
                "displacement_diagnostics": {
                    "path": str(diagnostics_path),
                    "sha256": file_artifact_hashes[
                        "displacement_diagnostics_csv_sha256"
                    ],
                },
                "registry": str(registry_path),
                "canonical_hashes": artifact_hashes,
            },
            "research": research,
            "decision": (
                "primary_pass_run_only_preregistered_robustness_and_stress_trials"
                if primary_passed
                else "reject_v3_without_threshold_side_or_date_slice_rescue"
            ),
        }
        serialized_report = (
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
        report_path.write_text(serialized_report, encoding="utf-8")
        registry_artifact_hashes = {
            **artifact_hashes,
            "report_json_sha256": file_sha256(report_path),
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
                    "by_side": research["by_side"],
                    "weekly": research["weekly"],
                    "statistical_confidence": research["statistical_confidence"],
                    "t1_primary_gates": research["t1_primary_gates"],
                    "artifact_hashes": registry_artifact_hashes,
                },
                status="passed_primary" if primary_passed else "rejected_primary",
            )
        )
        if not primary_passed:
            registry.complete(EXPERIMENT_ID)

    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run preregistered Overnight Displacement Reversal v3 train T1"
    )
    parser.add_argument(
        "--data", default="runs/multi-year/nq1_2021-03-16_2026-06-26.csv"
    )
    parser.add_argument(
        "--output-dir", default="runs/overnight-displacement-reversal-v3/train-t1"
    )
    parser.add_argument(
        "--registry",
        default="runs/overnight-displacement-reversal-v3/experiments.sqlite",
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
