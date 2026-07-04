import os
from pathlib import Path

import pytest

from full_python.cli import run_baseline
from full_python.reporting.metrics import build_metrics_report

FROZEN_SIMULATION_OVERRIDES_1NQ = {
    "point_value": 20.0,
    "commission_per_contract_round_trip": 10.0,
    "entry_slippage_points": 0.75,
    "exit_slippage_points": 0.75,
}
# MNQ = 1/10th the point value of NQ (2.0 vs 20.0), same tick/contract logic.
FROZEN_SIMULATION_OVERRIDES_1MNQ = {
    "point_value": 2.0,
    "commission_per_contract_round_trip": 1.0,
    "entry_slippage_points": 0.75,
    "exit_slippage_points": 0.75,
}


@pytest.mark.skipif(
    "FULL_PYTHON_BASELINE_DATA" not in os.environ,
    reason="requires the operator's local 9-month CSV; set FULL_PYTHON_BASELINE_DATA to run",
)
def test_1nq_vs_1mnq_sizing_comparison_on_the_frozen_window(tmp_path: Path) -> None:
    data_path = Path(os.environ["FULL_PYTHON_BASELINE_DATA"])

    nq_report_path = run_baseline(
        data_path=data_path,
        output_dir=tmp_path / "1nq",
        strategy_name="adaptive_trend_am",
        simulation_overrides=dict(FROZEN_SIMULATION_OVERRIDES_1NQ),
    )
    mnq_report_path = run_baseline(
        data_path=data_path,
        output_dir=tmp_path / "1mnq",
        strategy_name="adaptive_trend_am",
        simulation_overrides=dict(FROZEN_SIMULATION_OVERRIDES_1MNQ),
    )

    import json

    nq_report = json.loads(nq_report_path.read_text(encoding="utf-8"))
    mnq_report = json.loads(mnq_report_path.read_text(encoding="utf-8"))

    # Same signal core, same trade timing -- only point value/commission differ,
    # so trade COUNT must be identical; only P&L scales.
    assert nq_report["survivability"]["trade_count"] == mnq_report["survivability"]["trade_count"]
    # NQ P&L should be ~10x MNQ P&L (10x point value, 10x commission) minus the
    # commission-per-trade difference; assert the ratio is in a sane band rather
    # than an exact 10x (small-quantity rounding and AM-escalation trades make
    # exact 10x unlikely).
    if mnq_report["survivability"]["net_pnl"] != 0:
        ratio = nq_report["survivability"]["net_pnl"] / mnq_report["survivability"]["net_pnl"]
        assert 8.0 < ratio < 12.0
