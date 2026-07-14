"""TradingView parity is the engine's authority. Guard it.

The 2026-07-12 calendar regression removed three trades that TradingView had
actually taken, dropping trade-level parity from 106/106 to 103/106 -- and it
went unnoticed because the only committed artifact linking the engine to
TradingView (``golden_trades.json``) was regenerated from the engine's own new
output. A sim-vs-sim fixture cannot detect a sim-vs-TradingView regression.

The always-on test below pins the property that broke: the abbreviated holiday
sessions must be traded. The opt-in test performs the real reconciliation when
the operator's TradingView export is available.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_trades.json"

# Abbreviated CME holiday sessions (09:30-13:00 ET) inside the anchor window.
# The market is open, the entry window is open, and TradingView traded these.
ABBREVIATED_HOLIDAY_SESSIONS = {
    "2025-11-27",  # Thanksgiving
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-05-25",  # Memorial Day
}


@pytest.mark.skipif(not FIXTURE_PATH.exists(), reason="golden fixture not present")
def test_golden_anchor_still_trades_the_abbreviated_holiday_sessions() -> None:
    trades = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    traded = {t["session_date"] for t in trades} & ABBREVIATED_HOLIDAY_SESSIONS
    assert traded == ABBREVIATED_HOLIDAY_SESSIONS, (
        "the anchor no longer trades every abbreviated holiday session "
        f"(missing: {sorted(ABBREVIATED_HOLIDAY_SESSIONS - traded)}). These are open "
        "market sessions that TradingView traded; deleting them breaks trade-level "
        "parity. See docs/decisions/2026-07-13-exchange-calendar-correction.md."
    )


@pytest.mark.skipif(not FIXTURE_PATH.exists(), reason="golden fixture not present")
def test_golden_anchor_trade_count_matches_the_reconciled_window() -> None:
    trades = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    # 106 TradingView-matched + 9 that predate TradingView's 1-minute history.
    assert len(trades) == 115


@pytest.mark.skipif(
    "FULL_PYTHON_TV_EXPORT" not in os.environ
    or "FULL_PYTHON_BASELINE_DATA" not in os.environ,
    reason=(
        "requires the operator's TradingView export (FULL_PYTHON_TV_EXPORT) and the "
        "9-month anchor CSV (FULL_PYTHON_BASELINE_DATA)"
    ),
)
def test_engine_reconciles_106_of_106_against_the_tradingview_export(tmp_path) -> None:
    from full_python.cli import run_baseline
    from full_python.reconcile import load_sim_trades, load_tv_trades, reconcile
    from scripts.freeze_baseline_anchor import FROZEN_SIMULATION_OVERRIDES

    run_baseline(
        data_path=os.environ["FULL_PYTHON_BASELINE_DATA"],
        output_dir=tmp_path,
        strategy_name="adaptive_trend_am",
        simulation_overrides=dict(FROZEN_SIMULATION_OVERRIDES),
    )
    tv_trades = [
        trade
        for trade in load_tv_trades(os.environ["FULL_PYTHON_TV_EXPORT"])
        if trade.entry_time.isoformat() < "2026-06-27"
    ]
    report = reconcile(tv_trades, load_sim_trades(tmp_path / "trades.csv"))

    assert report.tv_trade_count == 106
    assert report.matched_count == 106, f"missing in sim: {report.missing_in_sim}"
    assert not report.missing_in_sim
    assert report.summary()["quantity_mismatches"] == 0
    assert report.summary()["max_abs_entry_price_delta"] == 0.0
