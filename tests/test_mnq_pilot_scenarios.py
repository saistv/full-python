from full_python.research.mnq_pilot import MNQ_PILOT_SCENARIOS, mnq_pilot_config


def test_mnq_pilot_candidate_is_flat_one_contract_with_150_dll() -> None:
    config = mnq_pilot_config()

    assert config.contracts == 1
    assert config.enable_anti_martingale is False
    assert config.max_contracts_per_entry == 1
    assert config.enable_daily_loss_limit is True
    assert config.daily_loss_limit == 150.0
    assert config.dollar_point_value == 2.0


def test_mnq_pilot_cost_axis_is_locked() -> None:
    assert [scenario.name for scenario in MNQ_PILOT_SCENARIOS] == [
        "reference_0_75pt",
        "stress_1_5pt",
    ]
    assert [scenario.slippage_points for scenario in MNQ_PILOT_SCENARIOS] == [
        0.75,
        1.5,
    ]
