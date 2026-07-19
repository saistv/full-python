import json
from pathlib import Path

import pytest

from full_python.cli import build_strategy
from full_python.research.registry import ExperimentRegistry
import scripts.run_opening_auction_retest_experiment as runner_module
from scripts.run_opening_auction_retest_experiment import (
    EXPERIMENT_ID,
    _session_boundary_utc,
    load_train_bars,
    run,
)


HEADER = "timestamp,symbol,open,high,low,close,volume\n"


def test_train_loader_never_constructs_an_excluded_session_bar(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    path.write_text(
        HEADER
        + "2024-12-31T22:59:00Z,NQ1!,100,101,99,100,10\n"
        + "2024-12-31T23:00:00Z,NQ1!,200,201,199,200,20\n"
        + "2025-01-02T14:30:00Z,NQ1!,300,301,299,300,30\n",
        encoding="utf-8",
    )
    bars, digest = load_train_bars(path, end_session_exclusive="2025-01-01")
    assert _session_boundary_utc("2025-01-01") == "2024-12-31T23:00:00Z"
    assert [bar.timestamp_utc for bar in bars] == ["2024-12-31T22:59:00Z"]
    assert bars[0].open == 100.0
    assert len(digest) == 64


def test_train_loader_detects_a_hidden_eligible_row_after_a_future_row(
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
        load_train_bars(path, end_session_exclusive="2025-01-01")


def test_runner_refuses_to_overwrite_before_touching_data(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        run(
            data_path=tmp_path / "missing.csv",
            output_dir=output,
            registry_path=tmp_path / "experiments.sqlite",
        )


def test_general_cli_cannot_bypass_the_sealed_research_runner() -> None:
    with pytest.raises(ValueError, match="Unknown strategy"):
        build_strategy("opening_auction_retest")


def test_runner_registers_before_replay_and_hashes_every_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_path = tmp_path / "single-bar.csv"
    data_path.write_text(
        HEADER + "2024-12-30T14:30:00Z,NQ1!,100,101,99,100,10\n",
        encoding="utf-8",
    )
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
    assert report["decision"] == "reject_v2_without_threshold_or_branch_salvage"
    for artifact_name in (
        "events",
        "trades",
        "auction_sessions",
        "auction_diagnostics",
    ):
        artifact = report["artifacts"][artifact_name]
        assert Path(artifact["path"]).is_file()
        assert len(artifact["sha256"]) == 64

    with ExperimentRegistry(registry_path) as registry:
        experiment = registry.experiment(EXPERIMENT_ID)
    assert experiment["status"] == "completed"
    assert len(experiment["trials"]) == 1
    artifact_hashes = experiment["trials"][0]["metrics"]["artifact_hashes"]
    assert artifact_hashes["evaluation_policy_sha256"] == report[
        "source_provenance"
    ]["evaluation_policy_sha256"]
    assert len(artifact_hashes["canonical_ledger_sha256"]) == 64
    assert len(artifact_hashes["research_result_sha256"]) == 64
