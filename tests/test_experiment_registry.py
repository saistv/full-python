import sqlite3

import pytest

from full_python.research.registry import ExperimentRegistry, ExperimentSpec, TrialRecord


def _spec(trial_budget: int = 2) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="exp-001",
        objective="Test one registered axis",
        hypothesis="The candidate improves every forward fold",
        data_hash="data",
        strategy_hash="strategy",
        simulation_hash="simulation",
        code_hash="code",
        trial_budget=trial_budget,
    )


def test_registry_preserves_preregistration_and_trials(tmp_path) -> None:
    path = tmp_path / "experiments.sqlite"
    with ExperimentRegistry(path) as registry:
        registry.register(_spec(), created_at_utc="2026-07-12T00:00:00Z")
        registry.record_trial(TrialRecord(
            experiment_id="exp-001",
            trial_index=1,
            config_hash="cell-a",
            overrides={"ma_50_length": 40},
            metrics={"net_pnl": 100.0},
            fold={"fold_index": 1},
        ), recorded_at_utc="2026-07-12T01:00:00Z")
        registry.complete("exp-001")
        record = registry.experiment("exp-001")

    assert record["status"] == "completed"
    assert record["trial_budget"] == 2
    assert record["trials"][0]["overrides"] == {"ma_50_length": 40}
    assert record["trials"][0]["metrics"] == {"net_pnl": 100.0}


def test_registry_enforces_trial_budget_and_unique_indices(tmp_path) -> None:
    with ExperimentRegistry(tmp_path / "experiments.sqlite") as registry:
        registry.register(_spec(trial_budget=1))
        trial = TrialRecord("exp-001", 1, "a", {}, {"net": 1})
        registry.record_trial(trial)
        with pytest.raises(ValueError, match="budget"):
            registry.record_trial(TrialRecord("exp-001", 2, "b", {}, {"net": 2}))


def test_completed_experiment_rejects_new_trials(tmp_path) -> None:
    with ExperimentRegistry(tmp_path / "experiments.sqlite") as registry:
        registry.register(_spec())
        registry.complete("exp-001")
        with pytest.raises(ValueError, match="closed"):
            registry.record_trial(TrialRecord("exp-001", 1, "a", {}, {}))
