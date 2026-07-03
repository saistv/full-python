"""Single-axis parameter perturbation harness.

Measures how sensitive a run is to one parameter at a time: for each
``--vary name=v1,v2,...`` axis, every value is simulated independently
against the same data (all other parameters held at baseline) and the
headline metrics are tabulated next to the baseline.

This is a MEASUREMENT tool, not an optimizer. A parameter whose neighbors
collapse is fragile; a flat neighborhood is robust. It deliberately does
not search, rank, or recommend — production changes still require the
full promotion gate, and single-axis sweeps cannot see interaction
effects between parameters.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path
from typing import Any

from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import AdaptiveTrendConfig, production_am_config

COLUMN_MAP = CsvBarColumnMap(
    timestamp="timestamp", symbol="symbol", open="open",
    high="high", low="low", close="close", volume="volume",
)


def _base_config(strategy_name: str) -> AdaptiveTrendConfig:
    if strategy_name == "adaptive_trend":
        return AdaptiveTrendConfig()
    if strategy_name == "adaptive_trend_am":
        return production_am_config()
    raise ValueError(f"Unsupported strategy for perturbation: {strategy_name}")


def _coerce(config: AdaptiveTrendConfig, name: str, raw: str) -> Any:
    current = getattr(config, name)  # AttributeError = unknown parameter
    if isinstance(current, bool):
        return raw.lower() in ("1", "true", "on", "yes")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def _metrics(trades, config: SimulationConfig) -> dict[str, Any]:
    pnls = [t.net_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    equity = peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return {
        "net_pnl": round(sum(pnls), 2),
        "trades": len(pnls),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "max_drawdown": round(max_dd, 2),
    }


def run_perturbation(
    *,
    data_path: str | Path,
    strategy_name: str,
    axes: dict[str, list[str]],
    simulation_config: SimulationConfig,
) -> dict[str, Any]:
    bars = load_csv_bars(Path(data_path), COLUMN_MAP)
    if not bars:
        raise ValueError(f"No bars loaded from {data_path}")
    base = _base_config(strategy_name)

    def simulate(config: AdaptiveTrendConfig) -> dict[str, Any]:
        sim_config = simulation_config
        if config.enable_daily_loss_limit and sim_config.daily_loss_limit is None:
            sim_config = dataclasses.replace(
                sim_config, daily_loss_limit=config.daily_loss_limit
            )
        result = SimulationEngine(sim_config).run(bars, AdaptiveTrendStrategy(config))
        return _metrics(result.trades, sim_config)

    baseline = simulate(base)
    axis_results: dict[str, list[dict[str, Any]]] = {}
    for name, raw_values in axes.items():
        rows = []
        for raw in raw_values:
            value = _coerce(base, name, raw)
            if value == getattr(base, name):
                rows.append({"value": value, "baseline": True, **baseline})
                continue
            variant = dataclasses.replace(base, name=f"{base.name}|{name}={value}", **{name: value})
            rows.append({"value": value, "baseline": False, **simulate(variant)})
        axis_results[name] = rows

    return {
        "strategy": strategy_name,
        "data": str(data_path),
        "baseline": {**baseline, **{"config": base.to_dict()}},
        "axes": axis_results,
    }


def _print_table(report: dict[str, Any]) -> None:
    baseline = report["baseline"]
    print(
        f"baseline: net {baseline['net_pnl']:+,.0f}  trades {baseline['trades']}  "
        f"wr {baseline['win_rate']:.1%}  pf {baseline['profit_factor']}  dd {baseline['max_drawdown']:,.0f}"
    )
    for axis, rows in report["axes"].items():
        print(f"\n{axis}:")
        for row in rows:
            marker = " *" if row["baseline"] else "  "
            delta = row["net_pnl"] - baseline["net_pnl"]
            print(
                f"{marker}{row['value']!s:>10}  net {row['net_pnl']:>+12,.0f}  "
                f"delta {delta:>+10,.0f}  trades {row['trades']:>4}  "
                f"pf {str(row['profit_factor']):>7}  dd {row['max_drawdown']:>10,.0f}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Single-axis parameter sensitivity sweeps.")
    parser.add_argument("--data", required=True)
    parser.add_argument("--strategy", default="adaptive_trend_am",
                        choices=["adaptive_trend", "adaptive_trend_am"])
    parser.add_argument(
        "--vary", action="append", required=True, metavar="NAME=V1,V2,...",
        help="Config field and comma-separated values; repeat for multiple axes",
    )
    parser.add_argument("--output", help="Optional JSON output path")
    parser.add_argument("--point-value", type=float, default=20.0)
    parser.add_argument("--commission-rt", type=float, default=10.0)
    parser.add_argument("--entry-slippage-points", type=float, default=0.75)
    parser.add_argument("--exit-slippage-points", type=float, default=0.75)
    parser.add_argument("--rth-open-extra-slippage-points", type=float, default=0.0)
    args = parser.parse_args()

    axes: dict[str, list[str]] = {}
    for spec in args.vary:
        name, _, values = spec.partition("=")
        if not values:
            raise SystemExit(f"--vary expects NAME=V1,V2,... got: {spec}")
        axes[name.strip()] = [v.strip() for v in values.split(",") if v.strip()]

    simulation_config = SimulationConfig(
        point_value=args.point_value,
        commission_per_contract_round_trip=args.commission_rt,
        entry_slippage_points=args.entry_slippage_points,
        exit_slippage_points=args.exit_slippage_points,
        rth_open_extra_entry_slippage_points=args.rth_open_extra_slippage_points,
    )
    report = run_perturbation(
        data_path=args.data, strategy_name=args.strategy,
        axes=axes, simulation_config=simulation_config,
    )
    if args.output:
        Path(args.output).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    _print_table(report)


if __name__ == "__main__":
    main()
