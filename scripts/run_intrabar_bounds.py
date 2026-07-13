"""Run and register the five-year NQ intrabar uncertainty report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from full_python.cli import _code_version_hash, _git_commit, _source_is_dirty
from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.manifest import file_sha256
from full_python.research.intrabar_bounds import build_intrabar_bounds_report
from full_python.research.registry import ExperimentRegistry, ExperimentSpec, TrialRecord
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import production_am_config


def run(*, data_path: Path, registry_path: Path, output_path: Path) -> Path:
    if _source_is_dirty():
        raise ValueError("refusing authority experiment from a dirty source tree")
    bars = load_csv_bars(data_path, CsvBarColumnMap(
        timestamp="timestamp", symbol="symbol", open="open", high="high",
        low="low", close="close", volume="volume",
    ))
    strategy_config = production_am_config()
    simulation = SimulationConfig(
        point_value=20.0,
        commission_per_contract_round_trip=10.0,
        entry_slippage_points=0.75,
        exit_slippage_points=0.75,
        rth_open_extra_entry_slippage_points=0.0,
        daily_loss_limit=strategy_config.daily_loss_limit,
    )
    spec = ExperimentSpec(
        experiment_id="phase2-nq-intrabar-bounds-v1",
        objective="Quantify MFE uncertainty from unknown one-minute bar paths",
        hypothesis="Intrabar ambiguity affects MFE claims but not frozen stop-first P&L",
        data_hash=file_sha256(data_path),
        strategy_hash=strategy_config.parameter_hash(),
        simulation_hash=simulation.parameter_hash(),
        code_hash=_code_version_hash(),
        trial_budget=1,
        notes="Measurement only; no target, exit, stop, or parameter changes.",
    )
    result = SimulationEngine(simulation).run(
        bars, AdaptiveTrendStrategy(strategy_config)
    )
    report = build_intrabar_bounds_report(result.trades, bars)
    metrics = report.to_dict()
    with ExperimentRegistry(registry_path) as registry:
        registry.register(spec)
        registry.record_trial(TrialRecord(
            experiment_id=spec.experiment_id,
            trial_index=1,
            config_hash=strategy_config.parameter_hash(),
            overrides={},
            metrics=metrics,
        ))
        registry.complete(spec.experiment_id)
    output = {
        "experiment_id": spec.experiment_id,
        "source_provenance": {
            "git_commit": _git_commit(),
            "source_tree_sha256": _code_version_hash(),
            "dirty": False,
        },
        "method": {
            "pnl_policy": "frozen stop-first",
            "mfe_lower": "confirmed before the stop bar",
            "mfe_upper": "favorable OHLC extreme on the stop bar",
        },
        "metrics": metrics,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="runs/multi-year/nq1_2021-03-16_2026-06-26.csv")
    parser.add_argument("--registry", default="runs/phase2-intrabar-bounds.sqlite")
    parser.add_argument("--output", default="runs/phase2-nq-intrabar-bounds.json")
    args = parser.parse_args()
    print(run(
        data_path=Path(args.data), registry_path=Path(args.registry),
        output_path=Path(args.output),
    ))


if __name__ == "__main__":
    main()
