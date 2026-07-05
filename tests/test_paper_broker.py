from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger
from full_python.execution.broker_protocol import Acked, BrokerPosition, Filled
from full_python.execution.paper_broker import PaperBroker
from full_python.models import MarketBar, OrderIntent, StrategyResult
from full_python.simulation import SimulationConfig


def _bar(ts, price):
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=price, high=price,
                     low=price, close=price, volume=1.0)


def _config():
    return SimulationConfig(point_value=2.0, commission_per_contract_round_trip=1.0,
                            entry_slippage_points=0.0, exit_slippage_points=0.0,
                            rth_open_extra_entry_slippage_points=0.0)


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


def _drive(broker, bar, result=None):
    session = classify_timestamp(bar.timestamp_utc)
    broker.process_bar_open(bar, session)
    broker.apply_strategy_result(bar, session, result or StrategyResult())
    broker.note_bar_processed(bar, session)
    return broker.poll_events()


def test_intent_acks_then_fills_at_next_bar_open():
    broker = PaperBroker(_config(), _NullStrategy(), EventLedger())
    bar1, bar2 = _bar("2025-10-01T14:31:00Z", 100.0), _bar("2025-10-01T14:32:00Z", 102.0)

    events1 = _drive(broker, bar1, _buy_result(bar1))
    assert events1 == [Acked(order_id="P1")]
    assert broker.position is None  # not filled yet

    events2 = _drive(broker, bar2)
    fills = [e for e in events2 if isinstance(e, Filled)]
    assert len(fills) == 1
    assert fills[0].order_id == "P1"
    assert fills[0].side == "buy"
    assert fills[0].price == 102.0  # next bar open, zero slippage
    assert broker.position == BrokerPosition(side="long", quantity=1, entry_price=102.0)


def test_flatten_produces_exit_fill_event_and_trade():
    broker = PaperBroker(_config(), _NullStrategy(), EventLedger())
    bar1, bar2 = _bar("2025-10-01T14:31:00Z", 100.0), _bar("2025-10-01T14:32:00Z", 102.0)
    _drive(broker, bar1, _buy_result(bar1))
    _drive(broker, bar2)

    bar3 = _bar("2025-10-01T14:33:00Z", 105.0)
    broker.flatten(bar3, "supervisor_halt")
    events = broker.poll_events()
    fills = [e for e in events if isinstance(e, Filled)]
    assert len(fills) == 1
    assert fills[0].order_id.startswith("X")
    assert fills[0].side == "sell"
    assert fills[0].reason == "supervisor_halt"
    assert broker.position is None
    assert len(broker.trades) == 1
    assert broker.trades[0].exit_reason == "supervisor_halt"


def test_daily_limit_hit_passthrough_defaults_false():
    broker = PaperBroker(_config(), _NullStrategy(), EventLedger())
    assert broker.daily_limit_hit is False
