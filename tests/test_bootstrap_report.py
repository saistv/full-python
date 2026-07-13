import pytest

from full_python.reporting.bootstrap import build_block_bootstrap_report


def test_block_bootstrap_is_deterministic_and_reports_adverse_drawdown() -> None:
    series = [100.0, -50.0, 0.0, 200.0, -125.0, 50.0] * 10

    first = build_block_bootstrap_report(
        series, block_length_sessions=5, draws=250, seed=7
    )
    second = build_block_bootstrap_report(
        series, block_length_sessions=5, draws=250, seed=7
    )

    assert first == second
    assert first.session_count == 60
    assert first.total_net_pnl_95.lower <= first.total_net_pnl_95.median
    assert first.total_net_pnl_95.median <= first.total_net_pnl_95.upper
    assert first.max_drawdown_p99_adverse <= first.max_drawdown_p95_adverse
    assert first.max_drawdown_p95_adverse <= first.max_drawdown_median
    assert 0.0 <= first.probability_total_net_nonpositive <= 1.0


def test_empty_bootstrap_is_explicitly_zero() -> None:
    report = build_block_bootstrap_report([], draws=10)

    assert report.session_count == 0
    assert report.total_net_pnl_95.lower == 0.0
    assert report.max_drawdown_p95_adverse == 0.0


def test_bootstrap_rejects_invalid_controls() -> None:
    with pytest.raises(ValueError, match="block_length"):
        build_block_bootstrap_report([1.0], block_length_sessions=0)
    with pytest.raises(ValueError, match="draws"):
        build_block_bootstrap_report([1.0], draws=0)
