from pathlib import Path

from full_python.execution.broker_protocol import BrokerPosition
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.models import MarketBar, Trade


def _bar(ts, price):
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=price, high=price,
                     low=price, close=price, volume=1.0)


def _trade(net, session):
    return Trade(symbol="NQ", side="long", quantity=1,
                 entry_timestamp_utc="x", entry_price=0.0, exit_timestamp_utc="x",
                 exit_price=0.0, exit_reason="test", stop_price=0.0,
                 gross_points=0.0, gross_pnl=net, commission=0.0, net_pnl=net,
                 mfe_points=0.0, mae_points=0.0, session_date=session)


def test_disabled_supervisor_never_breaches():
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=2.0))
    reason = sup.check_mark(session_date="2025-10-01", bar=_bar("t", 100.0),
                            position=None, trades=[_trade(-99999.0, "2025-10-01")])
    assert reason is None
    assert sup.entries_allowed() is True


def test_daily_loss_stop_includes_unrealized_and_latches():
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=2.0, daily_loss_stop=150.0))
    # realized -100; unrealized: long 1 from 100.0 marked at 70.0 -> -60 -> total -160
    pos = BrokerPosition(side="long", quantity=1, entry_price=100.0)
    reason = sup.check_mark(session_date="2025-10-01", bar=_bar("t", 70.0),
                            position=pos, trades=[_trade(-100.0, "2025-10-01")])
    assert reason == "supervisor_daily_loss"
    assert sup.entries_allowed() is False
    # latched even when the mark recovers:
    reason2 = sup.check_mark(session_date="2025-10-01", bar=_bar("t", 200.0),
                             position=None, trades=[_trade(-100.0, "2025-10-01")])
    assert reason2 == "supervisor_daily_loss"
    # resets on a new session:
    reason3 = sup.check_mark(session_date="2025-10-02", bar=_bar("t", 100.0),
                             position=None, trades=[_trade(-100.0, "2025-10-01")])
    assert reason3 is None
    assert sup.entries_allowed() is True


def test_max_position_and_kill_switch(tmp_path):
    switch = tmp_path / "halt"
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=2.0,
                                              max_position_contracts=2,
                                              kill_switch_path=switch))
    big = BrokerPosition(side="long", quantity=3, entry_price=100.0)
    assert sup.check_mark(session_date="s", bar=_bar("t", 100.0),
                          position=big, trades=[]) == "supervisor_max_position"

    sup2 = RiskSupervisor(RiskSupervisorConfig(point_value=2.0, kill_switch_path=switch))
    assert sup2.check_mark(session_date="s", bar=_bar("t", 100.0),
                           position=None, trades=[]) is None
    switch.write_text("stop")
    assert sup2.check_mark(session_date="s", bar=_bar("t", 100.0),
                           position=None, trades=[]) == "supervisor_kill_switch"
