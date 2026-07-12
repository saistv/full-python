"""Run and register the locked four-level NQ execution-cost axis."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from full_python.cli import _code_version_hash
from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.manifest import file_sha256
from full_python.reporting.survivability import TradeResult, build_survivability_report
from full_python.research.execution_scenarios import EXECUTION_SCENARIOS
from full_python.research.registry import ExperimentRegistry, ExperimentSpec, TrialRecord
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import production_am_config


def run(*, data_path: Path, registry_path: Path, output_path: Path) -> Path:
    bars = load_csv_bars(data_path, CsvBarColumnMap(
        timestamp="timestamp", symbol="symbol", open="open", high="high",
        low="low", close="close", volume="volume",
    ))
    base_strategy = production_am_config()
    base_sim = SimulationConfig(
        point_value=20.0,
        commission_per_contract_round_trip=10.0,
        entry_slippage_points=0.75,
        exit_slippage_points=0.75,
        rth_open_extra_entry_slippage_points=0.0,
        daily_loss_limit=base_strategy.daily_loss_limit,
    )
    spec = ExperimentSpec(
        experiment_id="phase2-nq-execution-cost-axis-v1",
        objective="Measure whether the locked NQ edge survives increasing fill friction",
        hypothesis="Net remains positive at every registered cost level",
        data_hash=file_sha256(data_path),
        strategy_hash=base_strategy.parameter_hash(),
        simulation_hash=base_sim.parameter_hash(),
        code_hash=_code_version_hash(),
        trial_budget=len(EXECUTION_SCENARIOS),
        notes="Cost sensitivity only; no latency, missed-fill, or queue model.",
    )
    output = {"experiment_id": spec.experiment_id, "scenarios": []}
    with ExperimentRegistry(registry_path) as registry:
        registry.register(spec)
        for index, scenario in enumerate(EXECUTION_SCENARIOS, start=1):
            sim = SimulationConfig(
                point_value=20.0,
                commission_per_contract_round_trip=10.0,
                entry_slippage_points=scenario.entry_slippage_points,
                exit_slippage_points=scenario.exit_slippage_points,
                rth_open_extra_entry_slippage_points=0.0,
                daily_loss_limit=base_strategy.daily_loss_limit,
            )
            result = SimulationEngine(sim).run(
                bars, AdaptiveTrendStrategy(base_strategy)
            )
            survivability = build_survivability_report([
                TradeResult(t.exit_timestamp_utc, t.side, t.net_pnl)
                for t in result.trades
            ])
            metrics = survivability.to_dict()
            row = {
                "scenario": scenario.name,
                "description": scenario.description,
                "entry_slippage_points": scenario.entry_slippage_points,
                "exit_slippage_points": scenario.exit_slippage_points,
                "simulation_hash": sim.parameter_hash(),
                "metrics": metrics,
            }
            output["scenarios"].append(row)
            registry.record_trial(TrialRecord(
                experiment_id=spec.experiment_id,
                trial_index=index,
                config_hash=sim.parameter_hash(),
                overrides={
                    "entry_slippage_points": scenario.entry_slippage_points,
                    "exit_slippage_points": scenario.exit_slippage_points,
                },
                metrics=metrics,
            ))
        registry.complete(spec.experiment_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="runs/multi-year/nq1_2021-03-16_2026-06-26.csv")
    parser.add_argument("--registry", default="runs/phase2-cost-experiments.sqlite")
    parser.add_argument("--output", default="runs/phase2-nq-execution-cost-axis.json")
    args = parser.parse_args()
    print(run(
        data_path=Path(args.data), registry_path=Path(args.registry),
        output_path=Path(args.output),
    ))


if __name__ == "__main__":
    main()
