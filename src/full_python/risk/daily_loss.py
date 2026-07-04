"""Equity-based daily-loss-limit halt check, extracted verbatim from
SimulationEngine._veto_reason and _check_daily_loss_limit.
"""
from __future__ import annotations

from typing import Optional


def check_daily_limit_halt(daily_limit_hit: bool) -> Optional[str]:
    if daily_limit_hit:
        return "daily_limit"
    return None


def is_daily_loss_breached(session_pnl: float, daily_loss_limit: Optional[float]) -> bool:
    """Matches engine.py:313 exactly: breach when session_pnl <= -limit."""
    if daily_loss_limit is None:
        return False
    return session_pnl <= -daily_loss_limit
