from full_python.research.execution_timing import EXECUTION_TIMING_SCENARIOS


def test_execution_timing_axis_is_locked() -> None:
    assert [scenario.name for scenario in EXECUTION_TIMING_SCENARIOS] == [
        "reference", "one_minute_latency", "ten_percent_missed",
        "latency_plus_missed",
    ]
    assert [scenario.entry_delay_bars for scenario in EXECUTION_TIMING_SCENARIOS] == [
        0, 1, 0, 1,
    ]
    assert [scenario.entry_fill_rate for scenario in EXECUTION_TIMING_SCENARIOS] == [
        1.0, 1.0, 0.90, 0.90,
    ]
    assert len({scenario.entry_fill_seed for scenario in EXECUTION_TIMING_SCENARIOS}) == 1
