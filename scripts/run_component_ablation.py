"""Run the registered one-component Adaptive Trend ablation."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from full_python.cli import _code_version_hash, _git_commit, _source_is_dirty
from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.manifest import file_sha256
from full_python.reporting.survivability import TradeResult, build_survivability_report
from full_python.research.component_ablation import COMPONENT_ABLATION_SCENARIOS
from full_python.research.registry import ExperimentRegistry, ExperimentSpec, TrialRecord
from full_python.research.walk_forward import build_anchored_folds, summarize_walk_forward
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
    baseline = production_am_config()
    simulation = SimulationConfig(
        point_value=20.0,
        commission_per_contract_round_trip=10.0,
        entry_slippage_points=0.75,
        exit_slippage_points=0.75,
        rth_open_extra_entry_slippage_points=0.0,
        daily_loss_limit=baseline.daily_loss_limit,
    )
    folds = build_anchored_folds(
        data_start="2021-03-16",
        initial_validation_start="2023-01-01",
        data_end="2026-06-27",
        validation_months=6,
    )
    spec = ExperimentSpec(
        experiment_id="phase2-nq-component-ablation-v1",
        objective="Measure the marginal role of each frozen entry confirmation",
        hypothesis="Removing any one confirmation worsens stability or survivability",
        data_hash=file_sha256(data_path),
        strategy_hash=baseline.parameter_hash(),
        simulation_hash=simulation.parameter_hash(),
        code_hash=_code_version_hash(),
        trial_budget=len(COMPONENT_ABLATION_SCENARIOS),
        notes=(
            "Diagnostic only: one removal per trial, no combinations, no "
            "parameter promotion; S/R break remains mandatory."
        ),
    )
    output = {
        "experiment_id": spec.experiment_id,
        "source_provenance": {
            "git_commit": _git_commit(),
            "source_tree_sha256": _code_version_hash(),
            "dirty": False,
        },
        "scenarios": [],
    }
    reference_metrics = None
    with ExperimentRegistry(registry_path) as registry:
        registry.register(spec)
        for index, scenario in enumerate(COMPONENT_ABLATION_SCENARIOS, start=1):
            strategy_config = replace(baseline, **scenario.overrides)
            result = SimulationEngine(simulation).run(
                bars, AdaptiveTrendStrategy(strategy_config)
            )
            survivability = build_survivability_report([
                TradeResult(t.exit_timestamp_utc, t.side, t.net_pnl)
                for t in result.trades
            ])
            fold_results = summarize_walk_forward(result.trades, folds)
            metrics = {
                **survivability.to_dict(),
                "positive_forward_folds": sum(f.net_pnl > 0 for f in fold_results),
                "forward_folds": [f.to_dict() for f in fold_results],
            }
            if reference_metrics is None:
                reference_metrics = metrics
            deltas = {
                "trade_count": metrics["trade_count"] - reference_metrics["trade_count"],
                "net_pnl": metrics["net_pnl"] - reference_metrics["net_pnl"],
                "max_drawdown": metrics["max_drawdown"] - reference_metrics["max_drawdown"],
                "positive_forward_folds": (
                    metrics["positive_forward_folds"]
                    - reference_metrics["positive_forward_folds"]
                ),
            }
            row = {
                "scenario": scenario.name,
                "description": scenario.description,
                "overrides": scenario.overrides,
                "strategy_hash": strategy_config.parameter_hash(),
                "metrics": metrics,
                "delta_vs_reference": deltas,
            }
            output["scenarios"].append(row)
            registry.record_trial(TrialRecord(
                experiment_id=spec.experiment_id,
                trial_index=index,
                config_hash=strategy_config.parameter_hash(),
                overrides=scenario.overrides,
                metrics={**metrics, "delta_vs_reference": deltas},
            ))
        registry.complete(spec.experiment_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="runs/multi-year/nq1_2021-03-16_2026-06-26.csv")
    parser.add_argument("--registry", default="runs/phase2-component-ablation.sqlite")
    parser.add_argument("--output", default="runs/phase2-nq-component-ablation.json")
    args = parser.parse_args()
    print(run(
        data_path=Path(args.data), registry_path=Path(args.registry),
        output_path=Path(args.output),
    ))


if __name__ == "__main__":
    main()
