"""Pre-registered execution-cost scenarios for baseline stress testing."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionScenario:
    name: str
    entry_slippage_points: float
    exit_slippage_points: float
    description: str


EXECUTION_SCENARIOS = (
    ExecutionScenario(
        "tv_matched", 0.75, 0.75,
        "TradingView-reconciled three-tick-per-side reference",
    ),
    ExecutionScenario(
        "adverse_1pt", 1.0, 1.0,
        "Four ticks per side",
    ),
    ExecutionScenario(
        "stress_1_5pt", 1.5, 1.5,
        "Six ticks per side",
    ),
    ExecutionScenario(
        "severe_2pt", 2.0, 2.0,
        "Eight ticks per side",
    ),
)
