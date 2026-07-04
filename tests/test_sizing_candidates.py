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

    # NOTE: trade count is NOT expected to be identical between the two runs.
    # The original assumption here ("same signal core -> same trade count,
    # only P&L scales") was wrong for adaptive_trend_am specifically: its
    # daily-loss-limit guard and projected-risk sizing cap are denominated
    # in DOLLARS ($1,000), not points, so the same point-distance stop
    # translates to a different effective dollar-risk budget at NQ's
    # point_value=20 vs MNQ's point_value=2 -- the guard blocks/allows a
    # different set of entries at each scale. Confirmed empirically: NQ
    # took 115 trades, MNQ took 129 (14 more, all previously blocked by the
    # DLL projected-risk guard at NQ's larger effective dollar risk per
    # point). This is itself a real, documented finding -- see
    # docs/decisions/2026-07-04-sizing-research-gate.md -- not a bug to
    # paper over with a looser assertion.
    assert nq_report["survivability"]["trade_count"] > 0
    assert mnq_report["survivability"]["trade_count"] > 0
    # NQ P&L should be roughly ~10x MNQ P&L (10x point value, 10x commission)
    # but not exactly, since the two runs don't even share the same trade
    # population (see above) -- assert a sane band, not an exact 10x.
    if mnq_report["survivability"]["net_pnl"] != 0:
        ratio = nq_report["survivability"]["net_pnl"] / mnq_report["survivability"]["net_pnl"]
        assert 8.0 < ratio < 12.0
