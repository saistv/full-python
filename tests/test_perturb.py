import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from full_python.perturb import _coerce, run_perturbation
from full_python.simulation import SimulationConfig
from full_python.strategy.adaptive_trend_config import AdaptiveTrendConfig


def test_coerce_respects_field_types() -> None:
    config = AdaptiveTrendConfig()

    assert _coerce(config, "prove_it_bars", "3") == 3
    assert _coerce(config, "wings_close_frac", "0.7") == 0.7
    assert _coerce(config, "enable_anti_martingale", "true") is True
    with pytest.raises(AttributeError):
        _coerce(config, "not_a_real_parameter", "1")


def _write_synthetic_csv(path: Path) -> None:
    price = 20000.0
    with path.open("w", encoding="utf-8") as handle:
        handle.write("timestamp,symbol,open,high,low,close,volume\n")
        for day in range(3):
            base = datetime(2026, 6, 29, 13, 30, tzinfo=timezone.utc) + timedelta(days=day)
            for minute in range(390):
                wobble = 6.0 * math.sin(minute / 9.0) + 2.5 * math.sin(minute / 2.3)
                open_ = price
                close = 20000.0 + 0.15 * minute + wobble + day * 3.0
                high = max(open_, close) + 1.5
                low = min(open_, close) - 1.5
                ts = (base + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")
                handle.write(f"{ts},NQ,{open_},{high},{low},{close},100\n")
                price = close


def test_perturbation_runs_axis_and_marks_baseline(tmp_path: Path) -> None:
    data = tmp_path / "bars.csv"
    _write_synthetic_csv(data)

    report = run_perturbation(
        data_path=data,
        strategy_name="adaptive_trend",
        axes={"prove_it_bars": ["1", "2", "3"]},
        simulation_config=SimulationConfig(point_value=20.0),
    )

    rows = report["axes"]["prove_it_bars"]
    assert [row["value"] for row in rows] == [1, 2, 3]
    baseline_rows = [row for row in rows if row["baseline"]]
    assert len(baseline_rows) == 1
    assert baseline_rows[0]["value"] == 2  # production prove-it
    assert baseline_rows[0]["net_pnl"] == report["baseline"]["net_pnl"]
    for row in rows:
        assert set(row) >= {"net_pnl", "trades", "win_rate", "profit_factor", "max_drawdown"}
