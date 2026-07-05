import pytest

from full_python.models import Trade
from full_python.research.sweep import (
    CellResult,
    _max_drawdown,
    _net,
    _net_without_top,
    _paired_session_t,
)


def _trade(net: float, side: str = "long", entry: str = "2023-05-01T14:31:00Z",
           session: str = "2023-05-01") -> Trade:
    return Trade(
        symbol="NQ", side=side, quantity=1,
        entry_timestamp_utc=entry, entry_price=100.0,
        exit_timestamp_utc=entry, exit_price=100.0,
        exit_reason="test", stop_price=99.0,
        gross_points=0.0, gross_pnl=net, commission=0.0, net_pnl=net,
        mfe_points=0.0, mae_points=0.0, session_date=session,
    )


def test_net_sums_net_pnl():
    trades = (_trade(500.0), _trade(-200.0), _trade(300.0))
    assert _net(trades) == 600.0
    assert _net(()) == 0.0


def test_max_drawdown_tracks_running_equity():
    # equity: 100, 50, -50, 150, 120 -> worst peak-to-trough = -50 -> -150
    trades = tuple(_trade(x) for x in (100.0, -50.0, -100.0, 200.0, -30.0))
    assert _max_drawdown(trades) == -150.0
    assert _max_drawdown(()) == 0.0
    # all-positive sequence never draws down
    assert _max_drawdown((_trade(10.0), _trade(20.0))) == 0.0


def test_net_without_top_removes_largest_winners():
    trades = tuple(_trade(x) for x in (500.0, -200.0, 400.0, -100.0, 300.0, 200.0))
    # net 1100; tops: 500, 400, 300
    assert _net_without_top(trades, 1) == 600.0
    assert _net_without_top(trades, 2) == 200.0
    assert _net_without_top(trades, 3) == -100.0


def test_paired_session_t_exact_value():
    # diffs per session: 50, 60, 40 -> mean 50, sample var 100, t = 5*sqrt(3)
    base = (
        _trade(100.0, session="2023-01-02"),
        _trade(200.0, session="2023-01-03"),
    )
    cell = (
        _trade(150.0, session="2023-01-02"),
        _trade(260.0, session="2023-01-03"),
        _trade(40.0, session="2023-01-04"),
    )
    t, n = _paired_session_t(cell, base)
    assert n == 3
    assert t == pytest.approx(8.6602540378, abs=1e-9)


def test_paired_session_t_boundary_two_point_zero():
    # diffs 10, 10, 40 -> mean 20, sample var 300, se 10, t exactly 2.0
    base = (
        _trade(100.0, session="2023-01-02"),
        _trade(200.0, session="2023-01-03"),
        _trade(300.0, session="2023-01-04"),
    )
    cell = (
        _trade(110.0, session="2023-01-02"),
        _trade(210.0, session="2023-01-03"),
        _trade(340.0, session="2023-01-04"),
    )
    t, n = _paired_session_t(cell, base)
    assert n == 3
    assert t == pytest.approx(2.0, abs=1e-12)


def test_paired_session_t_degenerate_cases_return_none():
    # single session -> None
    t, n = _paired_session_t((_trade(100.0, session="2023-01-02"),),
                             (_trade(50.0, session="2023-01-02"),))
    assert t is None and n == 1
    # identical populations -> zero variance -> None
    same = (_trade(100.0, session="2023-01-02"), _trade(200.0, session="2023-01-03"))
    t, n = _paired_session_t(same, same)
    assert t is None and n == 2


def test_cell_result_defaults():
    cell = CellResult(overrides={"ma_50_length": 40}, trades=())
    assert cell.error is None
    assert cell.config_hash is None
