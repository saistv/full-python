import json
from pathlib import Path

import pytest

from full_python.cli import build_strategy
from full_python.research.registry import ExperimentRegistry
import scripts.run_overnight_displacement_reversal_experiment as runner_module
from scripts.run_overnight_displacement_reversal_experiment import (
    EXPERIMENT_ID,
    load_bounded_train_bars,
    parse_canonical_utc_minute,
    run,
    session_boundary_text,
)


HEADER = "timestamp,symbol,open,high,low,close,volume\n"


def _single_train_bar(path: Path) -> None:
    path.write_text(
        HEADER + "2024-12-30T14:30:00Z,NQ1!,100,101,99,100,10\n",
        encoding="utf-8",
    )


def test_bounded_loader_never_parses_confirmation_ohlcv(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    path.write_text(
        HEADER
        + "2024-12-31T22:59:00Z,NQ1!,100,101,99,100,10\n"
        + "2024-12-31T23:00:00Z,DO_NOT_READ,not,parsed,as,prices,ever\n"
        + "2025-01-02T14:30:00Z,DO_NOT_READ,not,parsed,as,prices,ever\n",
        encoding="utf-8",
    )

    bars, digest = load_bounded_train_bars(
        path, end_session_exclusive="2025-01-01"
    )

    assert session_boundary_text("2025-01-01") == "2024-12-31T23:00:00Z"
    assert [bar.timestamp_utc for bar in bars] == ["2024-12-31T22:59:00Z"]
    assert bars[0].open == 100.0
    assert len(digest) == 64


@pytest.mark.parametrize(
    "timestamp",
    (
        "2024-12-30T14:30:01Z",
        "2024-12-30T14:30:00+00:00",
        "2024-12-30 14:30:00Z",
        "2024-02-30T14:30:00Z",
    ),
)
def test_canonical_timestamp_parser_fails_closed(timestamp: str) -> None:
    with pytest.raises(ValueError):
        parse_canonical_utc_minute(timestamp)


def test_bounded_loader_detects_out_of_order_row_beyond_boundary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "out-of-order.csv"
    path.write_text(
        HEADER
        + "2024-12-31T22:58:00Z,NQ1!,100,101,99,100,10\n"
        + "2025-01-02T14:30:00Z,NQ1!,300,301,299,300,30\n"
        + "2024-12-31T22:59:00Z,NQ1!,200,201,199,200,20\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not strictly increasing"):
        load_bounded_train_bars(path, end_session_exclusive="2025-01-01")


def test_runner_refuses_overwrite_before_touching_data(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        run(
            data_path=tmp_path / "missing.csv",
            output_dir=output,
            registry_path=tmp_path / "experiments.sqlite",
        )


def test_runner_requires_a_complete_finite_positive_capital_pair(
    tmp_path: Path,
) -> None:
    for allocated, hard_limit, message in (
        (10_000.0, None, "supplied together"),
        (float("nan"), 1_000.0, "allocated_capital"),
        (10_000.0, 0.0, "hard_loss_limit"),
    ):
        with pytest.raises(ValueError, match=message):
            run(
                data_path=tmp_path / "missing.csv",
                output_dir=tmp_path / f"output-{message}",
                registry_path=tmp_path / f"registry-{message}.sqlite",
                allocated_capital=allocated,
                hard_loss_limit=hard_limit,
            )


def test_general_cli_cannot_bypass_the_sealed_v3_runner() -> None:
    with pytest.raises(ValueError, match="Unknown strategy"):
        build_strategy("overnight_displacement_reversal")


def test_runner_preregisters_before_both_replays_and_hashes_every_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_path = tmp_path / "single-bar.csv"
    _single_train_bar(data_path)
    output_dir = tmp_path / "output"
    registry_path = tmp_path / "experiments.sqlite"
    original_run = runner_module.SimulationEngine.run
    registrations_seen: list[str] = []

    def run_after_registration(engine, bars, strategy):
        with ExperimentRegistry(registry_path) as registry:
            registrations_seen.append(registry.experiment(EXPERIMENT_ID)["status"])
        return original_run(engine, bars, strategy)

    monkeypatch.setattr(runner_module.SimulationEngine, "run", run_after_registration)
    report_path = run(
        data_path=data_path,
        output_dir=output_dir,
        registry_path=registry_path,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert registrations_seen == ["preregistered", "preregistered"]
    assert report["research"]["deterministic_replay"]["verified"] is True
    assert report["research"]["t1_primary_gates"]["passed"] is False
    assert report["decision"] == "reject_v3_without_threshold_side_or_date_slice_rescue"
    assert report["data"]["excluded_session_start_utc"] == "2024-12-31T23:00:00Z"
    assert report["evaluation_policy"]["maximum_warmup_expected_sessions"] == 25
    for artifact_name in (
        "events",
        "trades",
        "displacement_sessions",
        "displacement_diagnostics",
    ):
        artifact = report["artifacts"][artifact_name]
        assert Path(artifact["path"]).is_file()
        assert len(artifact["sha256"]) == 64
    assert Path(report["artifacts"]["displacement_sessions"]["path"]).name == (
        "displacement_sessions.csv"
    )
    assert Path(report["artifacts"]["displacement_diagnostics"]["path"]).name == (
        "displacement_diagnostics.csv"
    )

    canonical = report["artifacts"]["canonical_hashes"]
    for name in (
        "canonical_ledger_sha256",
        "canonical_trades_sha256",
        "canonical_session_dates_sha256",
        "canonical_snapshots_sha256",
        "canonical_diagnostics_sha256",
        "canonical_research_core_sha256",
        "research_result_sha256",
        "evaluation_policy_sha256",
        "strategy_config_sha256",
        "simulation_config_sha256",
        "source_tree_sha256",
        "hypothesis_sha256",
        "automation_standard_sha256",
        "full_file_sha256",
        "train_sequence_sha256",
        "registered_data_sha256",
    ):
        assert len(canonical[name]) == 64

    with ExperimentRegistry(registry_path) as registry:
        experiment = registry.experiment(EXPERIMENT_ID)
    assert experiment["status"] == "completed"
    assert len(experiment["trials"]) == 1
    artifact_hashes = experiment["trials"][0]["metrics"]["artifact_hashes"]
    assert len(artifact_hashes["report_json_sha256"]) == 64
    assert artifact_hashes["evaluation_policy_sha256"] == report[
        "source_provenance"
    ]["evaluation_policy_sha256"]


def test_deterministic_core_mismatch_is_a_fatal_t1_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_path = tmp_path / "single-bar.csv"
    _single_train_bar(data_path)
    output_dir = tmp_path / "output"
    registry_path = tmp_path / "experiments.sqlite"
    original_hashes = runner_module.core_replay_hashes
    calls = 0

    def mismatching_second_hash(result, strategy, research):
        nonlocal calls
        calls += 1
        hashes = original_hashes(result, strategy, research)
        if calls == 2:
            hashes["ledger"] = "0" * 64
        return hashes

    monkeypatch.setattr(
        runner_module, "core_replay_hashes", mismatching_second_hash
    )
    report_path = run(
        data_path=data_path,
        output_dir=output_dir,
        registry_path=registry_path,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    replay = report["research"]["deterministic_replay"]
    assert replay["verified"] is False
    assert replay["mismatches"] == ["ledger"]
    assert report["research"]["t1_primary_gates"]["passed"] is False
    with ExperimentRegistry(registry_path) as registry:
        assert registry.experiment(EXPERIMENT_ID)["status"] == "completed"
