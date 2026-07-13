from full_python.research.component_ablation import COMPONENT_ABLATION_SCENARIOS


def test_component_ablation_axis_is_locked_and_single_removal_only() -> None:
    assert [scenario.name for scenario in COMPONENT_ABLATION_SCENARIOS] == [
        "reference",
        "without_squeeze_momentum",
        "without_squeeze_release",
        "without_wings",
        "without_prove_it_hold",
    ]
    assert COMPONENT_ABLATION_SCENARIOS[0].overrides == {}
    for scenario in COMPONENT_ABLATION_SCENARIOS[1:]:
        assert len(scenario.overrides) == 1
        assert next(iter(scenario.overrides.values())) is False
