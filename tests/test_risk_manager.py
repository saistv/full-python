from __future__ import annotations

from full_python.data.sessions import classify_timestamp
from full_python.models import OrderIntent
from full_python.risk.risk_manager import RiskManager
from full_python.simulation.config import SimulationConfig


def _intent(*, side: str = "buy", quantity: int = 1, stop_price: float | None = 95.0) -> OrderIntent:
    metadata = {} if stop_price is None else {"stop_price": stop_price, "signal_price": 100.0}
    return OrderIntent.market_entry(
        timestamp_utc="2026-01-05T14:30:00Z",
        symbol="NQU2026",
        side=side,
        quantity=quantity,
        reason="test",
        metadata=metadata,
    )


def test_veto_reason_none_for_a_valid_intent_during_rth() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")  # RTH open window
    manager = RiskManager(SimulationConfig())

    result = manager.veto_reason(
        has_open_order=False,
        daily_limit_hit=False,
        session=session,
        intent=_intent(),
        reference_price=100.0,
    )

    assert result is None


def test_veto_reason_invalid_side() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")
    manager = RiskManager(SimulationConfig())
    bad_intent = _intent()
    object.__setattr__(bad_intent, "side", "hold")

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=False, session=session,
        intent=bad_intent, reference_price=100.0,
    )

    assert result == "invalid_side"


def test_veto_reason_invalid_quantity_over_max_contracts() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")
    manager = RiskManager(SimulationConfig(max_contracts=2))

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=False, session=session,
        intent=_intent(quantity=3), reference_price=100.0,
    )

    assert result == "invalid_quantity"


def test_veto_reason_position_already_open() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")
    manager = RiskManager(SimulationConfig())

    result = manager.veto_reason(
        has_open_order=True, daily_limit_hit=False, session=session,
        intent=_intent(), reference_price=100.0,
    )

    assert result == "position_already_open"


def test_veto_reason_daily_limit_hit_takes_priority_over_rth_check() -> None:
    session = classify_timestamp("2026-01-05T02:00:00Z")  # outside RTH AND daily limit hit
    manager = RiskManager(SimulationConfig())

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=True, session=session,
        intent=_intent(), reference_price=100.0,
    )

    assert result == "daily_limit"  # matches today's check order in engine.py:459-464


def test_veto_reason_outside_rth() -> None:
    # 13:00Z = 08:00 ET: pre-market, before RTH open, but still before the
    # 15:59 ET flatten cutoff -- isolates the outside_rth check. (The
    # brief's original fixture, 02:00Z = 21:00 ET the prior day, trips
    # after_flatten first since minutes_from_midnight_et=1260 >= 959; that
    # is confirmed to be true of today's unmodified engine.py too, so this
    # is a brief test-fixture fix, not a behavior change.)
    session = classify_timestamp("2026-01-05T13:00:00Z")
    manager = RiskManager(SimulationConfig(rth_entries_only=True))

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=False, session=session,
        intent=_intent(), reference_price=100.0,
    )

    assert result == "outside_rth"


def test_veto_reason_missing_stop() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")
    manager = RiskManager(SimulationConfig())

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=False, session=session,
        intent=_intent(stop_price=None), reference_price=100.0,
    )

    assert result == "missing_stop"


def test_veto_reason_invalid_stop_for_buy_above_reference() -> None:
    session = classify_timestamp("2026-01-05T14:30:00Z")
    manager = RiskManager(SimulationConfig())

    result = manager.veto_reason(
        has_open_order=False, daily_limit_hit=False, session=session,
        intent=_intent(stop_price=105.0), reference_price=100.0,
    )

    assert result == "invalid_stop"
