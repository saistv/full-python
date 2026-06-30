from full_python.strategy.config import BaselineMomentumConfig


def test_baseline_config_defaults_are_mnq_first_and_rth_promotable() -> None:
    config = BaselineMomentumConfig()

    assert config.instrument_for_risk == "MNQ"
    assert config.promote_session == "RTH"
    assert config.max_drawdown_dollars == 5000.0
    assert config.contract_multiplier == 2.0


def test_baseline_config_hash_changes_when_parameters_change() -> None:
    base = BaselineMomentumConfig()
    changed = BaselineMomentumConfig(breakout_lookback_bars=3)

    assert len(base.parameter_hash()) == 64
    assert base.parameter_hash() != changed.parameter_hash()
    assert base.to_dict()["breakout_lookback_bars"] == 2
