from full_python.execution.sweeps import ExitSweepConfig, run_exit_sweep
from full_python.models import MarketBar


def test_run_exit_sweep_ranks_parameter_combinations_by_net_pnl() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 100.0, 101.0, 99.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 102.0, 99.0, 101.0, 10),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 101.0, 131.0, 100.0, 130.0, 10),
        MarketBar("2026-06-30T13:33:00Z", "NQU2026", 130.0, 175.0, 129.0, 170.0, 10),
        MarketBar("2026-06-30T13:34:00Z", "NQU2026", 170.0, 172.0, 153.0, 155.0, 10),
    ]

    result = run_exit_sweep(
        bars,
        ExitSweepConfig(
            mfe_trailing_activation_points=(40.0, 80.0),
            mfe_trailing_giveback_points=(20.0,),
            fresh_breakout_clearance_points=(0.0,),
            cooldown_bars_after_exit=(0,),
            point_value=2.0,
            slippage_points_per_side=0.0,
            commission_per_contract=0.0,
        ),
    )

    assert result["combo_count"] == 2
    assert result["results"][0]["mfe_trailing_activation_points"] == 40.0
    assert result["results"][0]["total_net_pnl_dollars"] == 50.0
    assert result["results"][0]["exit_reason_counts"] == {"mfe_trailing_stop": 1}
    assert result["results"][1]["mfe_trailing_activation_points"] == 80.0
