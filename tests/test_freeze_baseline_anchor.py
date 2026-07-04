import json
from pathlib import Path

import pytest

from scripts.freeze_baseline_anchor import freeze_baseline_anchor


def test_freeze_baseline_anchor_writes_report_with_expected_config(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    # 3 bars is enough to prove the wiring; the real freeze uses FULL_PYTHON_BASELINE_DATA.
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-01-05T14:30:00Z,NQU2026,100,101,99,100,10\n"
        "2026-01-05T14:31:00Z,NQU2026,100,102,99,101,10\n"
        "2026-01-05T14:32:00Z,NQU2026,101,103,100,102.5,10\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "baseline-anchor"

    report_path = freeze_baseline_anchor(data_path=data_path, output_dir=output_dir)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    # NOTE: the frozen strategy is production_am_config() (name
    # "adaptive_trend_v66_am"), not the flat parity config -- the brief's
    # original draft of this assertion said "adaptive_trend_v66_flat",
    # which contradicts its own next two assertions (enable_anti_martingale
    # and enable_daily_loss_limit are only True on the AM config) and the
    # decision doc's stated strategy. Corrected to match actual behavior.
    assert report["strategy"]["name"] == "adaptive_trend_v66_am"
    assert report["strategy"]["enable_anti_martingale"] is True
    assert report["strategy"]["enable_daily_loss_limit"] is True
    assert report["simulation"]["point_value"] == 20.0
    assert report["simulation"]["commission_per_contract_round_trip"] == 10.0
    assert report["simulation"]["entry_slippage_points"] == 0.75
    assert report["simulation"]["exit_slippage_points"] == 0.75
    assert "code_version" in report
    assert "metrics" in report


def test_freeze_baseline_anchor_requires_env_var_when_no_path_given(monkeypatch) -> None:
    monkeypatch.delenv("FULL_PYTHON_BASELINE_DATA", raising=False)
    with pytest.raises(ValueError, match="FULL_PYTHON_BASELINE_DATA"):
        freeze_baseline_anchor(data_path=None, output_dir=Path("/tmp/unused"))
