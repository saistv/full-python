from __future__ import annotations

import pytest

from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger
from full_python.models import ExitDecision, MarketBar, OrderIntent, StrategyResult
from full_python.simulation.config import SimulationConfig
from full_python.simulation.position_engine import PositionEngine
from full_python.tradovate.errors import TradovateStateError
from full_python.tradovate.ledger import FillPairingLedger


def _ledger(commission: float = 1.0) -> FillPairingLedger:
    return FillPairingLedger(dollar_point_value=20.0, commission_per_contract_round_trip=commission)


def test_pairs_entry_and_exit_fills_into_a_trade() -> None:
    ledger = _ledger()
    ledger.open_leg(
        symbol="NQ", side="buy", quantity=1, price=100.0,
        timestamp_utc="2026-07-07T14:32:00Z", stop_price=95.0, session_date="2026-07-07",
    )
    assert ledger.has_open_leg

    trade = ledger.close_leg(price=110.0, timestamp_utc="2026-07-07T14:40:00Z", reason="atf_flip")

    assert not ledger.has_open_leg
    assert trade.side == "long"
    assert trade.gross_points == 10.0
    assert trade.gross_pnl == 200.0
    assert trade.commission == 1.0
    assert trade.net_pnl == 199.0
    assert trade.stop_price == 95.0
    assert trade.exit_reason == "atf_flip"
    assert trade.session_date == "2026-07-07"
    assert ledger.trades == [trade]


def test_short_leg_signs_and_excursions() -> None:
    ledger = _ledger()
    ledger.open_leg(
        symbol="NQ", side="sell", quantity=2, price=100.0,
        timestamp_utc="2026-07-07T14:32:00Z", stop_price=105.0, session_date="2026-07-07",
    )
    ledger.mark_bar(high=101.0, low=97.0)
    ledger.mark_bar(high=99.0, low=96.0)

    trade = ledger.close_leg(price=98.0, timestamp_utc="2026-07-07T14:45:00Z", reason="stop")

    assert trade.side == "short"
    assert trade.quantity == 2
    assert trade.gross_points == 2.0
    assert trade.gross_pnl == 80.0        # 2pt * $20 * 2 contracts
    assert trade.net_pnl == 78.0
    assert trade.mfe_points == 4.0        # entry 100 -> low 96
    assert trade.mae_points == 1.0        # entry 100 -> high 101


def test_realized_session_pnl_accumulates_per_session() -> None:
    ledger = _ledger()
    ledger.open_leg(symbol="NQ", side="buy", quantity=1, price=100.0,
                    timestamp_utc="2026-07-07T14:32:00Z", stop_price=95.0, session_date="2026-07-07")
    ledger.close_leg(price=95.0, timestamp_utc="2026-07-07T14:35:00Z", reason="stop")
    ledger.open_leg(symbol="NQ", side="buy", quantity=1, price=94.0,
                    timestamp_utc="2026-07-07T14:50:00Z", stop_price=90.0, session_date="2026-07-07")
    ledger.close_leg(price=90.0, timestamp_utc="2026-07-07T14:55:00Z", reason="stop")

    assert ledger.realized_session_pnl("2026-07-07") == pytest.approx(-182.0)  # (-100-1) + (-80-1)
    assert ledger.realized_session_pnl("2026-07-08") == 0.0


def test_double_open_and_orphan_close_raise() -> None:
    ledger = _ledger()
    with pytest.raises(TradovateStateError, match="no open leg"):
        ledger.close_leg(price=100.0, timestamp_utc="2026-07-07T14:32:00Z", reason="stop")

    ledger.open_leg(symbol="NQ", side="buy", quantity=1, price=100.0,
                    timestamp_utc="2026-07-07T14:32:00Z", stop_price=95.0, session_date="2026-07-07")
    with pytest.raises(TradovateStateError, match="already open"):
        ledger.open_leg(symbol="NQ", side="buy", quantity=1, price=101.0,
                        timestamp_utc="2026-07-07T14:33:00Z", stop_price=96.0, session_date="2026-07-07")


def test_trade_matches_position_engine_for_identical_fills() -> None:
    """Parity pin: identical fills through the sim and the ledger produce
    the identical Trade (zero-slippage sim config so fill prices match)."""
    config = SimulationConfig(
        point_value=20.0,
        commission_per_contract_round_trip=1.0,
        entry_slippage_points=0.0,
        exit_slippage_points=0.0,
        rth_open_extra_entry_slippage_points=0.0,
        rth_entries_only=False,
    )
    engine = PositionEngine(config, object(), EventLedger())

    def bar(ts: str, o: float, h: float, lo: float, c: float) -> MarketBar:
        return MarketBar(timestamp_utc=ts, symbol="NQ", open=o, high=h, low=lo, close=c, volume=1.0)

    bar1 = bar("2026-07-07T14:31:00Z", 100.0, 100.5, 99.5, 100.0)
    bar2 = bar("2026-07-07T14:32:00Z", 101.0, 103.0, 100.5, 102.0)
    bar3 = bar("2026-07-07T14:33:00Z", 102.5, 102.5, 102.5, 102.5)
    s1, s2, s3 = (classify_timestamp(b.timestamp_utc) for b in (bar1, bar2, bar3))

    engine.process_pre_strategy(bar1, s1)
    engine.apply_strategy_result(bar1, s1, StrategyResult(order_intents=(
        OrderIntent.market_entry(
            timestamp_utc=bar1.timestamp_utc, symbol="NQ", side="buy", quantity=1,
            reason="adaptive_trend", metadata={"stop_price": 95.0},
        ),
    )))
    engine.note_bar_processed(bar1, s1)
    engine.process_pre_strategy(bar2, s2)            # entry fills at bar2.open = 101.0
    engine.apply_strategy_result(bar2, s2, StrategyResult(exits=(
        ExitDecision(timestamp_utc=bar2.timestamp_utc, symbol="NQ", reason="atf_flip"),
    )))
    engine.note_bar_processed(bar2, s2)
    engine.process_pre_strategy(bar3, s3)            # exit fills at bar3.open = 102.5
    sim_trade = engine.trades[0]

    ledger = _ledger()
    ledger.open_leg(symbol="NQ", side="buy", quantity=1, price=101.0,
                    timestamp_utc=bar2.timestamp_utc, stop_price=95.0,
                    session_date=s2.session_date.isoformat())
    ledger.mark_bar(high=bar2.high, low=bar2.low)    # sim counts the entry bar's range
    trade = ledger.close_leg(price=102.5, timestamp_utc=bar3.timestamp_utc, reason="atf_flip")

    assert trade == sim_trade
