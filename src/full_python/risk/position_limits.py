"""Quantity and one-position-at-a-time checks, extracted verbatim from
SimulationEngine._veto_reason.
"""
from __future__ import annotations

from typing import Optional


def check_quantity(quantity: int, max_contracts: int) -> Optional[str]:
    if quantity < 1 or quantity > max_contracts:
        return "invalid_quantity"
    return None


def check_no_open_order(has_open_order: bool) -> Optional[str]:
    if has_open_order:
        return "position_already_open"
    return None
