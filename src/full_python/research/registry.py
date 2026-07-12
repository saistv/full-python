"""SQLite-backed experiment and trial registry.

The registry records the question and trial budget before results exist. Rows
are insert-only except for the experiment status transition; a duplicate trial
index or exhausted budget fails closed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Optional


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    objective: str
    hypothesis: str
    data_hash: str
    strategy_hash: str
    simulation_hash: str
    code_hash: str
    trial_budget: int
    parent_experiment_id: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id is required")
        if self.trial_budget < 1:
            raise ValueError("trial_budget must be positive")


@dataclass(frozen=True)
class TrialRecord:
    experiment_id: str
    trial_index: int
    config_hash: str
    overrides: dict[str, Any]
    metrics: dict[str, Any]
    fold: Optional[dict[str, Any]] = None
    status: str = "completed"

    def __post_init__(self) -> None:
        if self.trial_index < 1:
            raise ValueError("trial_index must be positive")


class ExperimentRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                created_at_utc TEXT NOT NULL,
                objective TEXT NOT NULL,
                hypothesis TEXT NOT NULL,
                data_hash TEXT NOT NULL,
                strategy_hash TEXT NOT NULL,
                simulation_hash TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                trial_budget INTEGER NOT NULL CHECK (trial_budget > 0),
                parent_experiment_id TEXT,
                notes TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('preregistered', 'running', 'completed', 'abandoned')
                ),
                FOREIGN KEY(parent_experiment_id) REFERENCES experiments(experiment_id)
            );
            CREATE TABLE IF NOT EXISTS trials (
                experiment_id TEXT NOT NULL,
                trial_index INTEGER NOT NULL CHECK (trial_index > 0),
                recorded_at_utc TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                overrides_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                fold_json TEXT,
                status TEXT NOT NULL,
                PRIMARY KEY(experiment_id, trial_index),
                FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
            );
        """)
        self._connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def register(self, spec: ExperimentSpec, *, created_at_utc: str | None = None) -> None:
        self._connection.execute(
            """INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                spec.experiment_id,
                created_at_utc or self._now(),
                spec.objective,
                spec.hypothesis,
                spec.data_hash,
                spec.strategy_hash,
                spec.simulation_hash,
                spec.code_hash,
                spec.trial_budget,
                spec.parent_experiment_id,
                spec.notes,
                "preregistered",
            ),
        )
        self._connection.commit()

    def record_trial(self, trial: TrialRecord, *, recorded_at_utc: str | None = None) -> None:
        experiment = self._connection.execute(
            "SELECT status, trial_budget FROM experiments WHERE experiment_id = ?",
            (trial.experiment_id,),
        ).fetchone()
        if experiment is None:
            raise ValueError(f"unknown experiment: {trial.experiment_id}")
        if experiment["status"] not in ("preregistered", "running"):
            raise ValueError(f"experiment is {experiment['status']}; trials are closed")
        count = self._connection.execute(
            "SELECT COUNT(*) FROM trials WHERE experiment_id = ?",
            (trial.experiment_id,),
        ).fetchone()[0]
        if count >= experiment["trial_budget"]:
            raise ValueError("trial budget exhausted")
        with self._connection:
            self._connection.execute(
                """INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trial.experiment_id,
                    trial.trial_index,
                    recorded_at_utc or self._now(),
                    trial.config_hash,
                    json.dumps(trial.overrides, sort_keys=True, separators=(",", ":")),
                    json.dumps(trial.metrics, sort_keys=True, separators=(",", ":")),
                    None if trial.fold is None else json.dumps(
                        trial.fold, sort_keys=True, separators=(",", ":")
                    ),
                    trial.status,
                ),
            )
            self._connection.execute(
                "UPDATE experiments SET status = 'running' WHERE experiment_id = ?",
                (trial.experiment_id,),
            )

    def complete(self, experiment_id: str, *, abandoned: bool = False) -> None:
        status = "abandoned" if abandoned else "completed"
        cursor = self._connection.execute(
            "UPDATE experiments SET status = ? WHERE experiment_id = ?",
            (status, experiment_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"unknown experiment: {experiment_id}")
        self._connection.commit()

    def experiment(self, experiment_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown experiment: {experiment_id}")
        result = dict(row)
        result["trials"] = self.trials(experiment_id)
        return result

    def trials(self, experiment_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM trials WHERE experiment_id = ? ORDER BY trial_index",
            (experiment_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["overrides"] = json.loads(item.pop("overrides_json"))
            item["metrics"] = json.loads(item.pop("metrics_json"))
            fold_json = item.pop("fold_json")
            item["fold"] = None if fold_json is None else json.loads(fold_json)
            result.append(item)
        return result

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "ExperimentRegistry":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
