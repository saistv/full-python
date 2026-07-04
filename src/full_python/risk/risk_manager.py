"""Shared risk-veto gate for a proposed order intent.

Behavior-preserving extraction from SimulationEngine._veto_reason
(simulation/engine.py:446-472) -- the exact same checks, in the exact
same order, so SimulationEngine's refactor to call this module is proven
unchanged by tests/test_golden_trades.py passing identically before and
after. Any live BrokerExecutionEngine (future work, Gate 5+) calls this
same module, never simulation-internal code.
"""
from __future__ import annotations

from typing import Optional

from full_python.data.sessions import SessionInfo
from full_python.models import OrderIntent
from full_python.risk.daily_loss import check_daily_limit_halt
from full_python.risk.position_limits import check_no_open_order, check_quantity
from full_python.risk.session_rules import check_after_flatten, check_rth_window
from full_python.simulation.config import SimulationConfig


class RiskManager:
    def __init__(self, config: SimulationConfig) -> None:
        self.config = config

    def veto_reason(
        self,
        *,
        has_open_order: bool,
        daily_limit_hit: bool,
        session: SessionInfo,
        intent: OrderIntent,
        reference_price: float,
    ) -> Optional[str]:
        if intent.side not in ("buy", "sell"):
            return "invalid_side"

        reason = check_quantity(intent.quantity, self.config.max_contracts)
        if reason is not None:
            return reason

        reason = check_no_open_order(has_open_order)
        if reason is not None:
            return reason

        reason = check_daily_limit_halt(daily_limit_hit)
        if reason is not None:
            return reason

        reason = check_after_flatten(session, self.config.flatten_minutes_et)
        if reason is not None:
            return reason

        reason = check_rth_window(session, self.config.rth_entries_only)
        if reason is not None:
            return reason

        if "stop_price" not in intent.metadata:
            return "missing_stop"

        stop_price = float(intent.metadata["stop_price"])
        if intent.side == "buy" and stop_price >= reference_price:
            return "invalid_stop"
        if intent.side == "sell" and stop_price <= reference_price:
            return "invalid_stop"
        return None
