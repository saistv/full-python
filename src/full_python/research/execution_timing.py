"""Pre-registered latency and missed-signal execution scenarios."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionTimingScenario:
    name: str
    entry_delay_bars: int
    entry_fill_rate: float
    entry_fill_seed: int
    description: str


EXECUTION_TIMING_SCENARIOS = (
    ExecutionTimingScenario(
        "reference", 0, 1.0, 20260713,
        "Normal next-bar-open delivery; no missed signals",
    ),
    ExecutionTimingScenario(
        "one_minute_latency", 1, 1.0, 20260713,
        "Fill one additional completed one-minute bar later",
    ),
    ExecutionTimingScenario(
        "ten_percent_missed", 0, 0.90, 20260713,
        "Deterministically omit approximately ten percent of entries",
    ),
    ExecutionTimingScenario(
        "latency_plus_missed", 1, 0.90, 20260713,
        "One-minute added latency plus the same deterministic omissions",
    ),
)
