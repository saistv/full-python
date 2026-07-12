import os
from pathlib import Path

import pytest

from full_python.cli import run_baseline
from full_python.reporting.metrics import build_metrics_report

from scripts.freeze_baseline_anchor import FROZEN_SIMULATION_OVERRIDES

FROZEN_SIMULATION_OVERRIDES_1NQ = dict(FROZEN_SIMULATION_OVERRIDES)
# MNQ = 1/10th the point value of NQ (2.0 vs 20.0), same tick/contract logic.
# Derived from the same frozen base so slippage-model fields (including
# rth_open_extra_entry_slippage_points) can't silently drift out of sync
# with the anchor's cost model -- see the anchor doc's real-data finding.
FROZEN_SIMULATION_OVERRIDES_1MNQ = {
    **FROZEN_SIMULATION_OVERRIDES,
    "point_value": 2.0,
    "commission_per_contract_round_trip": 1.0,
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

    # Dollar-denominated sizing and daily risk are intentionally evaluated
    # with the execution instrument's point value. NQ and MNQ can therefore
    # take different quantities and even different trade populations.
    assert nq_report["strategy"]["dollar_point_value"] == 20.0
    assert mnq_report["strategy"]["dollar_point_value"] == 2.0
    assert nq_report["simulation"]["point_value"] == 20.0
    assert mnq_report["simulation"]["point_value"] == 2.0
    assert nq_report["execution_instrument"]["root"] == "NQ"
    assert mnq_report["execution_instrument"]["root"] == "MNQ"
    assert nq_report["survivability"]["trade_count"] > 0
    assert mnq_report["survivability"]["trade_count"] > 0
