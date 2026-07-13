"""RTH-window and after-flatten checks, extracted verbatim from
SimulationEngine._veto_reason (see docs/decisions/2026-07-03-fill-simulation-policy.md
for the per-bar order these checks participate in).
"""
from __future__ import annotations

from typing import Optional

from full_python.data.sessions import SessionInfo


def check_after_flatten(session: SessionInfo, flatten_minutes_et: int) -> Optional[str]:
    if session.rth_close_minutes_et is None:
        return "market_closed"
    effective_flatten = min(flatten_minutes_et, session.rth_close_minutes_et - 1)
    if session.minutes_from_midnight_et >= effective_flatten:
        return "after_flatten"
    return None


def check_rth_window(session: SessionInfo, rth_entries_only: bool) -> Optional[str]:
    if rth_entries_only and not session.is_rth:
        return "outside_rth"
    return None
