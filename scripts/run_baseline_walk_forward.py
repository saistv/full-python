"""Register and report the locked baseline's anchored walk-forward folds."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from full_python.models import Trade
from full_python.research.registry import ExperimentRegistry, ExperimentSpec, TrialRecord
from full_python.research.walk_forward import build_anchored_folds, summarize_walk_forward


def _load_trades(path: Path) -> list[Trade]:
    trades = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            trades.append(Trade(
                symbol=row["symbol"], side=row["side"], quantity=int(row["quantity"]),
                entry_timestamp_utc=row["entry_timestamp_utc"],
                entry_price=float(row["entry_price"]),
                exit_timestamp_utc=row["exit_timestamp_utc"],
                exit_price=float(row["exit_price"]), exit_reason=row["exit_reason"],
                stop_price=float(row["stop_price"]),
                gross_points=float(row["gross_points"]),
                gross_pnl=float(row["gross_pnl"]), commission=float(row["commission"]),
                net_pnl=float(row["net_pnl"]), mfe_points=float(row["mfe_points"]),
                mae_points=float(row["mae_points"]), session_date=row["session_date"],
                ambiguous_exit=row["ambiguous_exit"] == "True",
            ))
    return trades


def _combined_hash(*values: str) -> str:
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def run(
    *, nq_report_path: Path, mnq_report_path: Path, registry_path: Path,
    output_path: Path,
) -> Path:
    nq_report = json.loads(nq_report_path.read_text(encoding="utf-8"))
    mnq_report = json.loads(mnq_report_path.read_text(encoding="utf-8"))
    folds = build_anchored_folds(
        data_start="2021-03-16",
        initial_validation_start="2023-01-01",
        data_end="2026-06-27",
        validation_months=6,
    )
    reports = {"NQ": nq_report, "MNQ": mnq_report}
    output: dict[str, object] = {
        "experiment_id": "phase2-baseline-walk-forward-v1",
        "folds": [fold.to_dict() for fold in folds],
        "instruments": {},
    }
    spec = ExperimentSpec(
        experiment_id="phase2-baseline-walk-forward-v1",
        objective="Measure baseline stability in separate anchored forward segments",
        hypothesis="The locked edge is positive in most six-month forward segments",
        data_hash=_combined_hash(
            nq_report["data"]["content_sha256"], mnq_report["data"]["content_sha256"]
        ),
        strategy_hash=_combined_hash(
            nq_report["strategy"]["parameter_hash"],
            mnq_report["strategy"]["parameter_hash"],
        ),
        simulation_hash=_combined_hash(
            nq_report["simulation"]["parameter_hash"],
            mnq_report["simulation"]["parameter_hash"],
        ),
        code_hash=nq_report["code_version"],
        trial_budget=2,
        notes="Baseline characterization only; no selection or parameter promotion.",
    )
    with ExperimentRegistry(registry_path) as registry:
        registry.register(spec)
        for index, (instrument, report) in enumerate(reports.items(), start=1):
            trades = _load_trades(Path(report["trades_path"]))
            fold_results = summarize_walk_forward(trades, folds)
            metrics = {
                "positive_folds": sum(result.net_pnl > 0 for result in fold_results),
                "fold_count": len(fold_results),
                "forward_net_pnl": sum(result.net_pnl for result in fold_results),
                "fold_results": [result.to_dict() for result in fold_results],
            }
            output["instruments"][instrument] = metrics
            registry.record_trial(TrialRecord(
                experiment_id=spec.experiment_id,
                trial_index=index,
                config_hash=report["strategy"]["parameter_hash"],
                overrides={"execution_instrument": instrument},
                metrics=metrics,
            ))
        registry.complete(spec.experiment_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nq-report", default="runs/phase1-authority-nq-5yr/report.json")
    parser.add_argument("--mnq-report", default="runs/phase1-authority-mnq-5yr/report.json")
    parser.add_argument("--registry", default="runs/experiments.sqlite")
    parser.add_argument("--output", default="runs/phase2-baseline-walk-forward.json")
    args = parser.parse_args()
    print(run(
        nq_report_path=Path(args.nq_report),
        mnq_report_path=Path(args.mnq_report),
        registry_path=Path(args.registry),
        output_path=Path(args.output),
    ))


if __name__ == "__main__":
    main()
