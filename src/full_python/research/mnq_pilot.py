"""Locked operational assumptions for the flat one-MNQ pilot."""
from __future__ import annotations

from dataclasses import dataclass, replace

from full_python.strategy.adaptive_trend_config import AdaptiveTrendConfig, production_am_config


@dataclass(frozen=True)
class MnqPilotScenario:
    name: str
    slippage_points: float
    description: str


MNQ_PILOT_SCENARIOS = (
    MnqPilotScenario(
        "reference_0_75pt", 0.75,
        "TradingView-reconciled three ticks per side",
    ),
    MnqPilotScenario(
        "stress_1_5pt", 1.5,
        "Six ticks per side",
    ),
)


def mnq_pilot_config() -> AdaptiveTrendConfig:
    return replace(
        production_am_config(),
        name="adaptive_trend_v66_mnq_pilot_flat1",
        contracts=1,
        enable_anti_martingale=False,
        max_contracts_per_entry=1,
        daily_loss_limit=150.0,
        dollar_point_value=2.0,
    )
