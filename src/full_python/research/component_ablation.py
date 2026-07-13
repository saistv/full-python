"""Locked one-component removals for Adaptive Trend diagnosis."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ComponentAblationScenario:
    name: str
    overrides: dict[str, Any]
    description: str


COMPONENT_ABLATION_SCENARIOS = (
    ComponentAblationScenario(
        "reference", {}, "Frozen production confirmation stack",
    ),
    ComponentAblationScenario(
        "without_squeeze_momentum",
        {"enable_squeeze_momentum_gate": False},
        "Remove directional and accelerating squeeze momentum",
    ),
    ComponentAblationScenario(
        "without_squeeze_release",
        {"enable_squeeze_release_gate": False},
        "Remove the squeeze released-state requirement",
    ),
    ComponentAblationScenario(
        "without_wings",
        {"enable_wings_gate": False},
        "Remove strong candle body and close-location confirmation",
    ),
    ComponentAblationScenario(
        "without_prove_it_hold",
        {"enable_prove_it_hold": False},
        "Enter from the initial S/R break without an additional hold bar",
    ),
)
