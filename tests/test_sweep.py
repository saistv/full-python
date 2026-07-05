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


from full_python.research.sweep import CellScore, score_cell


# ---------------------------------------------------------------------------
# All-pass fixture. Baseline: 4 trades/year x 3 years (2023-2025), each on
# its own session. Cell: the same 12 trades (same sessions -> paired diff 0)
# plus 2 extra winners/year on new sessions.
#
# Hand-computed: baseline net 3000 (1000/year), cell net 33000, delta 30000.
# Paired t: diffs = twelve 0s, three +5000s, three +4000s -> mean 1500,
# sum sq dev 82.5e6, sample var 4,852,941.18, se 519.2377, t = 2.88885.
# ---------------------------------------------------------------------------

def _shared_year(year: int) -> list:
    return [
        _trade(1000.0, "long", f"{year}-01-05T14:31:00Z", f"{year}-01-05"),
        _trade(-500.0, "long", f"{year}-02-05T14:31:00Z", f"{year}-02-05"),
        _trade(800.0, "short", f"{year}-03-05T14:31:00Z", f"{year}-03-05"),
        _trade(-300.0, "short", f"{year}-04-05T14:31:00Z", f"{year}-04-05"),
    ]


def _extras_year(year: int) -> list:
    return [
        _trade(5000.0, "long", f"{year}-05-05T14:31:00Z", f"{year}-05-05"),
        _trade(4000.0, "short", f"{year}-06-05T14:31:00Z", f"{year}-06-05"),
    ]


def _all_pass_pair() -> tuple[CellResult, CellResult]:
    baseline = tuple(t for y in (2023, 2024, 2025) for t in _shared_year(y))
    cell = tuple(t for y in (2023, 2024, 2025) for t in _shared_year(y) + _extras_year(y))
    return (
        CellResult(overrides={"ma_50_length": 40}, trades=cell),
        CellResult(overrides={}, trades=baseline),
    )


def test_score_cell_all_rows_pass():
    cell, baseline = _all_pass_pair()
    score = score_cell(cell, baseline)
    assert score.trade_count == 18
    assert score.net_pnl == 30000.0
    assert score.delta_vs_baseline == 27000.0
    for name, row in score.rows.items():
        assert row["pass"], f"row {name} unexpectedly failed: {row}"
    assert score.rows["trade_count"]["needs_justification"] is False
    assert score.rows["paired_t"]["t"] == pytest.approx(2.88885, abs=1e-4)
    assert score.rows["paired_t"]["n_sessions"] == 18
    assert score.passes_all is True


# ---------------------------------------------------------------------------
# Outlier-carried fixture. Baseline: 2023 has three +1000 longs plus
# (+800S, -500L, -300S, -800S); 2024/2025 have (+1000L, +800S, -500L,
# -300S, -800S). Baseline net 2600, count 17, max DD -1600.
# Cell: 2023's three +1000 longs degraded to +500 (same sessions), plus a
# single +14000 long on a new session. Cell net 15100, delta 12500.
#
# Expected: passes materiality/expectancy/drawdown/year_by_year/
# side_symmetry, trade_count unflagged, but FAILS outlier_survival
# (15100-14000=1100 < baseline-without-top1 1600) and FAILS paired_t
# (t = 0.8858 -- a single-session gain is not a reliable daily edge).
# The two failures are correlated by design: the paired t and the outlier
# cut are both built to catch exactly this shape. The spec's testing
# section sketched this fixture as failing only row 5; the paired t
# co-failing is mathematically inherent (3 outlier sessions out of 18
# cannot clear t>=2) and row 5's isolated logic is already covered by
# test_net_without_top_removes_largest_winners.
# ---------------------------------------------------------------------------

def _outlier_baseline_year(year: int) -> list:
    if year == 2023:
        return [
            _trade(1000.0, "long", "2023-01-05T14:31:00Z", "2023-01-05"),
            _trade(1000.0, "long", "2023-01-06T14:31:00Z", "2023-01-06"),
            _trade(1000.0, "long", "2023-01-07T14:31:00Z", "2023-01-07"),
            _trade(800.0, "short", "2023-02-05T14:31:00Z", "2023-02-05"),
            _trade(-500.0, "long", "2023-03-05T14:31:00Z", "2023-03-05"),
            _trade(-300.0, "short", "2023-04-05T14:31:00Z", "2023-04-05"),
            _trade(-800.0, "short", "2023-05-05T14:31:00Z", "2023-05-05"),
        ]
    return [
        _trade(1000.0, "long", f"{year}-01-05T14:31:00Z", f"{year}-01-05"),
        _trade(800.0, "short", f"{year}-02-05T14:31:00Z", f"{year}-02-05"),
        _trade(-500.0, "long", f"{year}-03-05T14:31:00Z", f"{year}-03-05"),
        _trade(-300.0, "short", f"{year}-04-05T14:31:00Z", f"{year}-04-05"),
        _trade(-800.0, "short", f"{year}-05-05T14:31:00Z", f"{year}-05-05"),
    ]


def test_score_cell_outlier_carried_gain_fails_outlier_and_t_rows():
    baseline_trades = tuple(
        t for y in (2023, 2024, 2025) for t in _outlier_baseline_year(y)
    )
    cell_trades = []
    for t in _outlier_baseline_year(2023):
        if t.side == "long" and t.net_pnl == 1000.0:
            cell_trades.append(
                _trade(500.0, "long", t.entry_timestamp_utc, t.session_date)
            )
        else:
            cell_trades.append(t)
    cell_trades.append(_trade(14000.0, "long", "2023-07-05T14:31:00Z", "2023-07-05"))
    cell_trades += _outlier_baseline_year(2024) + _outlier_baseline_year(2025)

    score = score_cell(
        CellResult(overrides={"ma_50_length": 30}, trades=tuple(cell_trades)),
        CellResult(overrides={}, trades=baseline_trades),
    )
    assert score.net_pnl == 15100.0
    assert score.delta_vs_baseline == 12500.0
    assert score.rows["materiality"]["pass"] is True
    assert score.rows["expectancy"]["pass"] is True
    assert score.rows["trade_count"]["needs_justification"] is False
    assert score.rows["drawdown"]["pass"] is True
    assert score.rows["year_by_year"]["pass"] is True
    assert score.rows["side_symmetry"]["pass"] is True
    assert score.rows["outlier_survival"]["pass"] is False
    assert score.rows["paired_t"]["pass"] is False
    assert score.rows["paired_t"]["t"] == pytest.approx(0.8858, abs=1e-3)
    assert score.passes_all is False


def test_score_cell_trade_count_drop_is_flag_only():
    # Cell keeps only 2 of baseline's 12 trades (an >20% drop) but with
    # massively improved trades everywhere it does fire -- every scored row
    # can still pass while needs_justification flips on.
    _, baseline = _all_pass_pair()
    cell_trades = tuple(
        _trade(net, side, f"{y}-01-05T14:31:00Z", f"{y}-01-05")
        for y, net, side in (
            (2023, 9000.0, "long"), (2023, 8000.0, "short"),
            (2024, 9000.0, "long"), (2024, 8000.0, "short"),
            (2025, 9000.0, "long"), (2025, 8000.0, "short"),
        )
    )
    score = score_cell(CellResult(overrides={"ma_50_length": 70}, trades=cell_trades), baseline)
    assert score.trade_count == 6
    assert score.rows["trade_count"]["needs_justification"] is True
    assert score.rows["trade_count"]["pass"] is True  # flag-only, never fails


def test_score_cell_empty_cell_fails_without_crashing():
    _, baseline = _all_pass_pair()
    score = score_cell(CellResult(overrides={"ma_50_length": 30}, trades=()), baseline)
    assert score.trade_count == 0
    assert score.net_pnl == 0.0
    assert score.rows["materiality"]["pass"] is False
    assert score.rows["expectancy"]["pass"] is False
    assert score.passes_all is False
