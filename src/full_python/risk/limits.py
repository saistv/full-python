"""Broker- and engine-agnostic limits consumed by RiskManager.

Extracted from the three SimulationConfig fields RiskManager actually
reads, so the risk layer no longer imports simulation internals and a
live execution engine (Gate 5+) can construct limits without a
SimulationConfig. See docs/superpowers/specs/2026-07-05-execution-core-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    max_contracts: int
    flatten_minutes_et: int  # minutes from midnight ET (e.g. 15:59 -> 959)
    rth_entries_only: bool
