from full_python.reporting.survivability import build_daily_metrics, build_monthly_breakdown


def test_flat_days_count_in_sharpe_and_day_rates() -> None:
    daily_pnl = {"2026-06-29": 200.0, "2026-07-01": -100.0}
    calendar = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"]

    metrics = build_daily_metrics(daily_pnl, calendar)

    assert metrics.trading_days == 4
    assert metrics.days_with_trades == 2
    assert metrics.profitable_days == 1
    assert metrics.losing_days == 1
    assert metrics.profitable_day_rate == 0.25
    assert metrics.avg_daily_pnl == 25.0
    assert metrics.best_day_pnl == 200.0
    assert metrics.worst_day_pnl == -100.0
    assert metrics.best_day_share == 2.0  # 200 best day on 100 net
    assert metrics.pnl_without_top_1_day == -100.0
    assert metrics.pnl_without_top_3_days == -100.0
    assert metrics.pnl_without_top_5_days == -100.0
    assert metrics.pnl_without_top_10_days == -100.0
    assert metrics.top_5_day_share == 2.0
    assert metrics.sharpe_annualized != 0.0


def test_time_underwater_counts_days_below_peak() -> None:
    daily_pnl = {
        "2026-06-29": 100.0,
        "2026-06-30": -50.0,
        "2026-07-01": -30.0,
        "2026-07-02": 100.0,
    }
    calendar = sorted(daily_pnl)

    metrics = build_daily_metrics(daily_pnl, calendar)

    # Two days ended below the peak; the final day reclaims it.
    assert metrics.max_time_underwater_days == 2

    unrecovered = dict(daily_pnl)
    unrecovered["2026-07-02"] = 10.0  # not enough to reclaim the peak
    assert build_daily_metrics(unrecovered, calendar).max_time_underwater_days == 3


def test_best_day_share_is_none_when_net_not_positive() -> None:
    metrics = build_daily_metrics({"2026-06-29": -100.0}, ["2026-06-29"])

    assert metrics.best_day_share is None


def test_monthly_breakdown_groups_by_calendar_month() -> None:
    monthly = build_monthly_breakdown(
        {"2026-06-29": 100.0, "2026-06-30": -40.0, "2026-07-01": 25.0}
    )

    assert monthly["2026-06"] == {"net_pnl": 60.0, "days_with_trades": 2}
    assert monthly["2026-07"] == {"net_pnl": 25.0, "days_with_trades": 1}
