from full_python.models import Trade
from full_python.reporting.metrics import (
    build_exit_reason_breakdown,
    build_expectancy_report,
    initial_risk_points,
    max_win_streak,
    r_multiple,
)


def _trade(
    *,
    side: str = "long",
    entry_price: float = 100.0,
    exit_price: float = 105.0,
    stop_price: float = 95.0,
    exit_reason: str = "atf_flip",
    net_pnl: float = 100.0,
    gross_pnl: float = 100.0,
    quantity: int = 1,
) -> Trade:
    return Trade(
        symbol="NQU2026",
        side=side,
        quantity=quantity,
        entry_timestamp_utc="2026-01-05T14:30:00Z",
        entry_price=entry_price,
        exit_timestamp_utc="2026-01-05T14:45:00Z",
        exit_price=exit_price,
        exit_reason=exit_reason,
        stop_price=stop_price,
        gross_points=exit_price - entry_price if side == "long" else entry_price - exit_price,
        gross_pnl=gross_pnl,
        commission=1.0,
        net_pnl=net_pnl,
        mfe_points=5.0,
        mae_points=1.0,
        session_date="2026-01-05",
    )


def test_initial_risk_points_is_distance_from_entry_to_frozen_stop() -> None:
    trade = _trade(entry_price=100.0, stop_price=95.0)
    assert initial_risk_points(trade) == 5.0


def test_initial_risk_points_handles_short_side_symmetrically() -> None:
    trade = _trade(side="short", entry_price=100.0, stop_price=105.0)
    assert initial_risk_points(trade) == 5.0


def test_r_multiple_expresses_net_pnl_in_units_of_initial_dollar_risk() -> None:
    # risk = 5 points * $20/point * 1 contract = $100; net_pnl=$250 -> R=2.5
    trade = _trade(entry_price=100.0, stop_price=95.0, net_pnl=250.0)
    assert r_multiple(trade, point_value=20.0) == 2.5


def test_r_multiple_is_none_when_stop_equals_entry() -> None:
    trade = _trade(entry_price=100.0, stop_price=100.0)
    assert r_multiple(trade, point_value=20.0) is None


def test_expectancy_report_computes_win_rate_and_average_win_loss() -> None:
    trades = [
        _trade(net_pnl=200.0, entry_price=100.0, stop_price=95.0),
        _trade(net_pnl=-100.0, entry_price=100.0, stop_price=95.0),
        _trade(net_pnl=0.0, entry_price=100.0, stop_price=95.0),
    ]

    report = build_expectancy_report(trades, point_value=20.0)

    assert report.trade_count == 3
    assert report.win_count == 1
    assert report.loss_count == 1
    assert report.scratch_count == 1
    assert report.win_rate == 1 / 3
    assert report.avg_win_dollars == 200.0
    assert report.avg_loss_dollars == 100.0
    assert report.expectancy_dollars == (200.0 - 100.0 + 0.0) / 3
    # R for each: 200/100=2.0, -100/100=-1.0, 0/100=0.0
    assert report.avg_r_multiple == (2.0 - 1.0 + 0.0) / 3
    assert report.r_multiples_computed == 3


def test_expectancy_report_on_empty_trades_is_all_zero_not_a_crash() -> None:
    report = build_expectancy_report([], point_value=20.0)

    assert report.trade_count == 0
    assert report.win_rate == 0.0
    assert report.avg_r_multiple is None


def test_exit_reason_breakdown_groups_and_sorts_by_reason() -> None:
    trades = [
        _trade(exit_reason="stop", net_pnl=-100.0, entry_price=100.0, stop_price=95.0),
        _trade(exit_reason="atf_flip", net_pnl=200.0, entry_price=100.0, stop_price=95.0),
        _trade(exit_reason="atf_flip", net_pnl=50.0, entry_price=100.0, stop_price=95.0),
    ]

    buckets = build_exit_reason_breakdown(trades, point_value=20.0)

    assert [b.exit_reason for b in buckets] == ["atf_flip", "stop"]
    atf_bucket = buckets[0]
    assert atf_bucket.trade_count == 2
    assert atf_bucket.net_pnl == 250.0
    assert atf_bucket.win_rate == 1.0


def test_max_win_streak_counts_consecutive_wins_and_resets_on_non_win() -> None:
    trades = [
        _trade(net_pnl=10.0),
        _trade(net_pnl=10.0),
        _trade(net_pnl=-5.0),
        _trade(net_pnl=10.0),
        _trade(net_pnl=10.0),
        _trade(net_pnl=10.0),
    ]

    assert max_win_streak(trades) == 3
