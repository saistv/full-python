from full_python.research.execution_scenarios import EXECUTION_SCENARIOS


def test_execution_cost_axis_is_locked_and_monotonic() -> None:
    assert [scenario.name for scenario in EXECUTION_SCENARIOS] == [
        "tv_matched", "adverse_1pt", "stress_1_5pt", "severe_2pt",
    ]
    costs = [scenario.entry_slippage_points for scenario in EXECUTION_SCENARIOS]
    assert costs == [0.75, 1.0, 1.5, 2.0]
    assert all(
        scenario.entry_slippage_points == scenario.exit_slippage_points
        for scenario in EXECUTION_SCENARIOS
    )
