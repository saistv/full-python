"""Run and register flat one-MNQ pilot suitability evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from full_python.cli import _code_version_hash, _git_commit, _source_is_dirty
from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.manifest import file_sha256
from full_python.reporting.bootstrap import build_block_bootstrap_report
from full_python.reporting.survivability import (
    TradeResult,
    build_daily_metrics,
    build_survivability_report,
)
from full_python.research.mnq_pilot import MNQ_PILOT_SCENARIOS, mnq_pilot_config
from full_python.research.pilot_risk import build_pilot_path_report
from full_python.research.registry import ExperimentRegistry, ExperimentSpec, TrialRecord
from full_python.research.walk_forward import build_anchored_folds, summarize_walk_forward
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy


def run(*, data_path: Path, registry_path: Path, output_path: Path) -> Path:
    if _source_is_dirty():
        raise ValueError("refusing authority experiment from a dirty source tree")
    bars = load_csv_bars(data_path, CsvBarColumnMap(
        timestamp="timestamp", symbol="symbol", open="open", high="high",
        low="low", close="close", volume="volume",
    ))
    strategy_config = mnq_pilot_config()
    reference_simulation = SimulationConfig(
        point_value=2.0,
        commission_per_contract_round_trip=1.0,
        entry_slippage_points=0.75,
        exit_slippage_points=0.75,
        rth_open_extra_entry_slippage_points=0.0,
        max_contracts=1,
        daily_loss_limit=150.0,
    )
    folds = build_anchored_folds(
        data_start="2021-03-16",
        initial_validation_start="2023-01-01",
        data_end="2026-06-27",
        validation_months=6,
    )
    spec = ExperimentSpec(
        experiment_id="mnq-flat1-pilot-sizing-v1",
        objective="Test whether flat one MNQ fits the fixed pilot loss budgets",
        hypothesis=(
            "Reference net is positive with >=4/7 positive folds, <=5% "
            "30-session loss-budget breaches, p95 drawdown within $500, "
            "and stressed net remains positive"
        ),
        data_hash=file_sha256(data_path),
        strategy_hash=strategy_config.parameter_hash(),
        simulation_hash=reference_simulation.parameter_hash(),
        code_hash=_code_version_hash(),
        trial_budget=len(MNQ_PILOT_SCENARIOS),
        notes=(
            "Operational sizing only: flat one MNQ, AM off, $150 daily DLL, "
            "$500 cumulative pilot stop, frozen signals."
        ),
    )
    output = {
        "experiment_id": spec.experiment_id,
        "source_provenance": {
            "git_commit": _git_commit(),
            "source_tree_sha256": _code_version_hash(),
            "dirty": False,
        },
        "strategy": {
            **strategy_config.to_dict(),
            "parameter_hash": strategy_config.parameter_hash(),
        },
        "scenarios": [],
    }
    with ExperimentRegistry(registry_path) as registry:
        registry.register(spec)
        for index, scenario in enumerate(MNQ_PILOT_SCENARIOS, start=1):
            simulation = SimulationConfig(
                point_value=2.0,
                commission_per_contract_round_trip=1.0,
                entry_slippage_points=scenario.slippage_points,
                exit_slippage_points=scenario.slippage_points,
                rth_open_extra_entry_slippage_points=0.0,
                max_contracts=1,
                daily_loss_limit=150.0,
            )
            result = SimulationEngine(simulation).run(
                bars, AdaptiveTrendStrategy(strategy_config)
            )
            daily_pnl: dict[str, float] = {}
            for trade in result.trades:
                daily_pnl[trade.session_date] = (
                    daily_pnl.get(trade.session_date, 0.0) + trade.net_pnl
                )
            daily_series = [daily_pnl.get(day, 0.0) for day in result.session_dates]
            survivability = build_survivability_report([
                TradeResult(t.exit_timestamp_utc, t.side, t.net_pnl)
                for t in result.trades
            ])
            fold_results = summarize_walk_forward(result.trades, folds)
            metrics = {
                "survivability": survivability.to_dict(),
                "daily": build_daily_metrics(
                    daily_pnl, list(result.session_dates)
                ).to_dict(),
                "full_history_bootstrap": build_block_bootstrap_report(
                    daily_series
                ).to_dict(),
                "pilot_30_sessions": build_pilot_path_report(
                    daily_series,
                    horizon_sessions=30,
                    loss_budget=500.0,
                    income_target=5000.0,
                ).to_dict(),
                "income_21_sessions": build_pilot_path_report(
                    daily_series,
                    horizon_sessions=21,
                    loss_budget=500.0,
                    income_target=5000.0,
                    seed=20260714,
                ).to_dict(),
                "positive_forward_folds": sum(f.net_pnl > 0 for f in fold_results),
                "forward_folds": [f.to_dict() for f in fold_results],
            }
            output["scenarios"].append({
                "scenario": scenario.name,
                "description": scenario.description,
                "slippage_points_per_side": scenario.slippage_points,
                "simulation_hash": simulation.parameter_hash(),
                "metrics": metrics,
            })
            registry.record_trial(TrialRecord(
                experiment_id=spec.experiment_id,
                trial_index=index,
                config_hash=simulation.parameter_hash(),
                overrides={"slippage_points_per_side": scenario.slippage_points},
                metrics=metrics,
            ))
        registry.complete(spec.experiment_id)

    reference = output["scenarios"][0]["metrics"]
    stress = output["scenarios"][1]["metrics"]
    gate_rows = {
        "reference_net_positive": reference["survivability"]["net_pnl"] > 0,
        "reference_positive_folds_at_least_4_of_7": (
            reference["positive_forward_folds"] >= 4
        ),
        "reference_30_session_budget_breach_at_most_5pct": (
            reference["pilot_30_sessions"]["probability_loss_budget_breached"] <= 0.05
        ),
        "reference_30_session_p95_drawdown_within_500": (
            reference["pilot_30_sessions"]["max_drawdown_p95_adverse"] >= -500.0
        ),
        "stress_net_positive": stress["survivability"]["net_pnl"] > 0,
    }
    output["pilot_gate"] = {
        "rows": gate_rows,
        "passes_all": all(gate_rows.values()),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="runs/multi-year/nq1_2021-03-16_2026-06-26.csv")
    parser.add_argument("--registry", default="runs/mnq-pilot-sizing.sqlite")
    parser.add_argument("--output", default="runs/mnq-pilot-sizing.json")
    args = parser.parse_args()
    print(run(
        data_path=Path(args.data), registry_path=Path(args.registry),
        output_path=Path(args.output),
    ))


if __name__ == "__main__":
    main()
