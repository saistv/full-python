from full_python.reporting.survivability import TradeResult, build_survivability_report


def test_survivability_report_calculates_drawdown_and_loss_streak() -> None:
    trades = [
        TradeResult("2026-06-30T13:31:00Z", "long", 100.0),
        TradeResult("2026-06-30T13:35:00Z", "long", -50.0),
        TradeResult("2026-06-30T13:40:00Z", "long", -75.0),
        TradeResult("2026-06-30T13:45:00Z", "long", 25.0),
    ]

    report = build_survivability_report(trades)

    assert report.net_pnl == 0.0
    assert report.max_drawdown == -125.0
    assert report.max_loss_streak == 2
    assert report.trade_count == 4


def test_survivability_report_tracks_top_trade_dependency() -> None:
    trades = [
        TradeResult("2026-06-30T13:31:00Z", "long", 500.0),
        TradeResult("2026-06-30T13:35:00Z", "long", -100.0),
        TradeResult("2026-06-30T13:40:00Z", "short", 50.0),
    ]

    report = build_survivability_report(trades)

    assert report.net_pnl == 450.0
    assert report.pnl_without_best_trade == -50.0
    assert report.long_pnl == 400.0
    assert report.short_pnl == 50.0
    assert report.win_rate == 2 / 3
    assert report.expectancy_per_trade == 150.0
    assert report.profit_factor == 5.5
    assert report.pnl_without_top_3_trades == -100.0
    assert report.pnl_without_top_5_trades == -100.0
    assert report.pnl_without_top_10_trades == -100.0
