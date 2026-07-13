from full_python.research.pilot_risk import build_pilot_path_report


def test_all_loss_paths_exhaust_budget() -> None:
    report = build_pilot_path_report(
        [-25.0] * 60,
        horizon_sessions=30,
        loss_budget=500.0,
        income_target=5000.0,
        block_length_sessions=10,
        draws=100,
        seed=7,
    )

    assert report.ending_pnl_95.lower == -750.0
    assert report.ending_pnl_95.median == -750.0
    assert report.probability_loss_budget_breached == 1.0
    assert report.probability_positive_end == 0.0
    assert report.probability_income_target_met == 0.0
    assert report.max_drawdown_p95_adverse == -750.0
    assert report.observed_window_loss_budget_breach_rate == 1.0


def test_all_gain_paths_meet_reachable_target_without_drawdown() -> None:
    report = build_pilot_path_report(
        [10.0] * 40,
        horizon_sessions=20,
        loss_budget=500.0,
        income_target=200.0,
        block_length_sessions=5,
        draws=50,
        seed=9,
    )

    assert report.ending_pnl_95.median == 200.0
    assert report.probability_loss_budget_breached == 0.0
    assert report.probability_positive_end == 1.0
    assert report.probability_income_target_met == 1.0
    assert report.max_drawdown_p95_adverse == 0.0
    assert report.observed_window_loss_budget_breach_rate == 0.0


def test_pilot_path_report_is_deterministic() -> None:
    series = [20.0, -35.0, 0.0, 45.0, -10.0] * 20
    first = build_pilot_path_report(series, draws=200, seed=42)
    second = build_pilot_path_report(series, draws=200, seed=42)

    assert first == second
