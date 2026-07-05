import json
import os
from pathlib import Path

import pytest

from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import production_am_config

from scripts.freeze_baseline_anchor import FROZEN_SIMULATION_OVERRIDES

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_trades.json"


def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason=(
        "golden_trades.json not yet produced — run scripts/freeze_baseline_anchor.py "
        "then scripts/export_golden_trades.py against the real 9-month dataset first"
    ),
)
def test_golden_trade_fixture_exists_and_is_nonempty() -> None:
    trades = _load_fixture()
    assert len(trades) > 0
    assert "entry_price" in trades[0]
    assert "exit_reason" in trades[0]


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason=(
        "golden_trades.json not yet produced — run scripts/freeze_baseline_anchor.py "
        "then scripts/export_golden_trades.py against the real 9-month dataset first"
    ),
)
def test_golden_trade_fixture_am_quantities_show_the_reconciled_escalation() -> None:
    # docs/decisions/2026-07-03-m2b-am-dll-reconciliation.md: 103x(1,1), 1x(2,2), 2x(3,3)
    trades = _load_fixture()
    quantities = [int(t["quantity"]) for t in trades if t["exit_reason"] != "session_end"]
    assert max(quantities) >= 2  # AM did escalate at least once in the frozen window


@pytest.mark.skipif(
    "FULL_PYTHON_BASELINE_DATA" not in os.environ or not FIXTURE_PATH.exists(),
    reason=(
        "requires the operator's local 9-month CSV (set FULL_PYTHON_BASELINE_DATA) "
        "and a committed golden_trades.json fixture; both are absent in this environment"
    ),
)
def test_replaying_the_frozen_window_reproduces_the_golden_fixture_exactly() -> None:
    column_map = CsvBarColumnMap(
        timestamp="timestamp", symbol="symbol", open="open",
        high="high", low="low", close="close", volume="volume",
    )
    bars = load_csv_bars(Path(os.environ["FULL_PYTHON_BASELINE_DATA"]), column_map)
    config = production_am_config()
    strategy = AdaptiveTrendStrategy(config)
    simulation_config = SimulationConfig(**FROZEN_SIMULATION_OVERRIDES)
    result = SimulationEngine(simulation_config).run(bars, strategy)

    replayed = [trade.to_payload() for trade in result.trades]
    golden = _load_fixture()

    assert len(replayed) == len(golden)
    for replayed_trade, golden_trade in zip(replayed, golden):
        assert replayed_trade["entry_timestamp_utc"] == golden_trade["entry_timestamp_utc"]
        assert replayed_trade["exit_timestamp_utc"] == golden_trade["exit_timestamp_utc"]
        assert replayed_trade["exit_reason"] == golden_trade["exit_reason"]
        assert replayed_trade["entry_price"] == pytest.approx(float(golden_trade["entry_price"]))
        assert replayed_trade["exit_price"] == pytest.approx(float(golden_trade["exit_price"]))
        assert replayed_trade["net_pnl"] == pytest.approx(float(golden_trade["net_pnl"]))
