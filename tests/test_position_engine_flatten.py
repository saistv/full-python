from full_python.events import EventLedger, EventType
from full_python.models import MarketBar, OrderIntent, StrategyResult
from full_python.data.sessions import classify_timestamp
from full_python.simulation import SimulationConfig
from full_python.simulation.position_engine import PositionEngine


def _bar(ts, price):
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=price, high=price,
                     low=price, close=price, volume=1.0)


class _NullStrategy:
    def on_bar(self, bar):
        return StrategyResult()


def _buy_result(bar):
    return StrategyResult(order_intents=(
        OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc, symbol="NQ", side="buy",
            quantity=1, reason="test",
            metadata={"stop_price": bar.close - 30.0, "signal_price": bar.close},
        ),
    ))


def test_flatten_now_closes_open_position_at_bar_close():
    ledger = EventLedger()
    config = SimulationConfig(point_value=2.0, commission_per_contract_round_trip=1.0,
                              entry_slippage_points=0.0, exit_slippage_points=0.0,
                              rth_open_extra_entry_slippage_points=0.0)
    engine = PositionEngine(config, _NullStrategy(), ledger)
    bar1 = _bar("2025-10-01T14:31:00Z", 100.0)
    bar2 = _bar("2025-10-01T14:32:00Z", 104.0)
    s1 = classify_timestamp(bar1.timestamp_utc)
    s2 = classify_timestamp(bar2.timestamp_utc)

    engine.process_pre_strategy(bar1, s1)
    engine.apply_strategy_result(bar1, s1, _buy_result(bar1))
    engine.note_bar_processed(bar1, s1)
    engine.process_pre_strategy(bar2, s2)  # entry fills at bar2 open
    assert engine.position is not None

    engine.flatten_now(bar2, "supervisor_halt")
    assert engine.position is None
    assert len(engine.trades) == 1
    assert engine.trades[0].exit_reason == "supervisor_halt"
    assert engine.trades[0].exit_price == 104.0  # bar close, zero slippage config


def test_flatten_now_cancels_pending_entry_and_is_noop_when_flat():
    ledger = EventLedger()
    config = SimulationConfig(point_value=2.0, commission_per_contract_round_trip=1.0,
                              entry_slippage_points=0.0, exit_slippage_points=0.0,
                              rth_open_extra_entry_slippage_points=0.0)
    engine = PositionEngine(config, _NullStrategy(), ledger)
    bar1 = _bar("2025-10-01T14:31:00Z", 100.0)
    s1 = classify_timestamp(bar1.timestamp_utc)

    engine.flatten_now(bar1, "supervisor_halt")  # flat + nothing pending: no-op
    assert len(engine.trades) == 0

    engine.process_pre_strategy(bar1, s1)
    engine.apply_strategy_result(bar1, s1, _buy_result(bar1))  # pending entry now
    engine.flatten_now(bar1, "supervisor_halt")
    cancel_events = [r for r in ledger.records
                     if r.event_type == EventType.STATE_TRANSITION
                     and r.payload.get("transition") == "pending_orders_cancelled"]
    assert len(cancel_events) == 1
    assert cancel_events[0].payload.get("reason") == "supervisor_halt"
    # cancelled pending entry never fills:
    bar2 = _bar("2025-10-01T14:32:00Z", 101.0)
    engine.process_pre_strategy(bar2, classify_timestamp(bar2.timestamp_utc))
    assert engine.position is None


def _run_backstop_case(signal_ts, fill_ts, backstop_ts):
    ledger = EventLedger()
    config = SimulationConfig(
        point_value=2.0,
        commission_per_contract_round_trip=0.0,
        entry_slippage_points=0.0,
        exit_slippage_points=0.0,
        rth_open_extra_entry_slippage_points=0.0,
    )
    engine = PositionEngine(config, _NullStrategy(), ledger)
    signal = _bar(signal_ts, 100.0)
    fill = _bar(fill_ts, 101.0)
    backstop = _bar(backstop_ts, 103.0)

    s1 = classify_timestamp(signal.timestamp_utc)
    engine.process_pre_strategy(signal, s1)
    engine.apply_strategy_result(signal, s1, _buy_result(signal))
    engine.note_bar_processed(signal, s1)
    s2 = classify_timestamp(fill.timestamp_utc)
    engine.process_pre_strategy(fill, s2)
    engine.note_bar_processed(fill, s2)
    assert engine.position is not None

    engine.process_pre_strategy(backstop, classify_timestamp(backstop.timestamp_utc))
    return engine, backstop


def test_scheduled_early_close_backstops_at_1314_in_simulation():
    # Day after Thanksgiving 2025 closes at 13:15 ET, so the backstop is 13:14.
    engine, backstop = _run_backstop_case(
        "2025-11-28T18:12:00Z",  # 13:12 ET
        "2025-11-28T18:13:00Z",  # 13:13 ET
        "2025-11-28T18:14:00Z",  # 13:14 ET -- one minute before the close
    )
    assert engine.position is None
    assert engine.trades[-1].exit_reason == "session_flatten"
    assert engine.trades[-1].exit_timestamp_utc == backstop.timestamp_utc


def test_abbreviated_holiday_session_backstops_at_1259_in_simulation():
    # Thanksgiving 2025 trades 09:30-13:00 ET, so the backstop is 12:59.
    engine, backstop = _run_backstop_case(
        "2025-11-27T17:57:00Z",  # 12:57 ET
        "2025-11-27T17:58:00Z",  # 12:58 ET
        "2025-11-27T17:59:00Z",  # 12:59 ET -- one minute before the close
    )
    assert engine.position is None
    assert engine.trades[-1].exit_reason == "session_flatten"
    assert engine.trades[-1].exit_timestamp_utc == backstop.timestamp_utc
