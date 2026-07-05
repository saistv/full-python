"""Shared risk-veto gate for a proposed order intent.

Behavior-preserving extraction from SimulationEngine._veto_reason
(simulation/engine.py:446-472) -- the exact same checks, in the exact
same order, so SimulationEngine's refactor to call this module is proven
unchanged by tests/test_golden_trades.py passing identically before and
after. Any live BrokerExecutionEngine (future work, Gate 5+) calls this
same module, never simulation-internal code. Decoupled from SimulationConfig
via risk.limits.RiskLimits (2026-07-05); SimulationEngine constructs the
limits at init.
"""
from __future__ import annotations

from typing import Optional

from full_python.data.sessions import SessionInfo
from full_python.models import OrderIntent
from full_python.risk.daily_loss import check_daily_limit_halt
from full_python.risk.limits import RiskLimits
from full_python.risk.position_limits import check_no_open_order, check_quantity
from full_python.risk.session_rules import check_after_flatten, check_rth_window


class RiskManager:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def veto_reason(
        self,
        *,
        has_open_order: bool,
        daily_limit_hit: bool,
        session: SessionInfo,
        intent: OrderIntent,
        reference_price: float,
    ) -> Optional[str]:
        """Evaluate veto reasons for a proposed order intent.

        Args:
            has_open_order: Whether a position or pending order exists.
            daily_limit_hit: Whether the session daily-loss limit has been breached.
            session: Current trading session info.
            intent: The proposed order intent.
            reference_price: Entry price reference (expected to be pre-computed by caller).
                Eager evaluation: reference_price is computed before any veto checks run,
                not lazily inside the final invalid_stop branches. This is a deliberate
                simplification from the original SimulationEngine._veto_reason inline logic
                and is safe only because the current codebase always populates
                intent.metadata["signal_price"] with a numeric value (bar.close).
                A future caller that breaks this contract would hit potential failures eagerly.

        Returns:
            A veto reason string if the intent should be rejected, else None.
        """
        if intent.side not in ("buy", "sell"):
            return "invalid_side"

        reason = check_quantity(intent.quantity, self.limits.max_contracts)
        if reason is not None:
            return reason

        reason = check_no_open_order(has_open_order)
        if reason is not None:
            return reason

        reason = check_daily_limit_halt(daily_limit_hit)
        if reason is not None:
            return reason

        reason = check_after_flatten(session, self.limits.flatten_minutes_et)
        if reason is not None:
            return reason

        reason = check_rth_window(session, self.limits.rth_entries_only)
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
