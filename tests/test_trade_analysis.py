from pathlib import Path

from full_python.reporting.trade_analysis import build_trade_analysis, load_trade_csv


def test_trade_analysis_calculates_periods_drawdown_streaks_and_dependency(tmp_path: Path) -> None:
    trades_path = tmp_path / "trades.csv"
    trades_path.write_text(
        "trade_id,symbol,side,quantity,entry_timestamp_utc,entry_price,exit_timestamp_utc,"
        "exit_price,exit_reason,stop_price,pnl_points,gross_pnl_dollars,"
        "commission_dollars,net_pnl_dollars,max_favorable_excursion_points,"
        "max_adverse_excursion_points\n"
        "trade-00000001,NQH6,long,1,2026-01-05T14:30:00Z,100,2026-01-05T14:40:00Z,"
        "95,stop,95,-5,-10,2,-12,3,-5\n"
        "trade-00000002,NQH6,long,1,2026-01-06T14:30:00Z,100,2026-01-06T14:40:00Z,"
        "120,symbol_change,95,20,40,2,38,22,-1\n"
        "trade-00000003,NQM6,short,1,2026-02-03T14:30:00Z,100,2026-02-03T14:40:00Z,"
        "103,stop,103,-3,-6,2,-8,7,-3\n"
        "trade-00000004,NQM6,long,1,2026-02-04T14:30:00Z,100,2026-02-04T14:40:00Z,"
        "90,stop,90,-10,-20,2,-22,1,-10\n",
        encoding="utf-8",
    )

    analysis = build_trade_analysis(load_trade_csv(trades_path))

    assert analysis["summary"]["trade_count"] == 4
    assert analysis["summary"]["total_net_pnl_dollars"] == -4.0
    assert analysis["summary"]["win_rate"] == 0.25
    assert analysis["risk"]["max_drawdown_dollars"] == -30.0
    assert analysis["risk"]["max_loss_streak"] == 2
    assert analysis["top_trade_dependency"]["best_trade_net_pnl_dollars"] == 38.0
    assert analysis["top_trade_dependency"]["pnl_without_best_1_trades"] == -42.0
    assert analysis["top_trade_dependency"]["pnl_without_best_3_trades"] == -22.0
    assert analysis["monthly_breakdown"]["2026-01"]["net_pnl_dollars"] == 26.0
    assert analysis["monthly_breakdown"]["2026-02"]["net_pnl_dollars"] == -30.0
    assert analysis["quarterly_breakdown"]["2026-Q1"]["trade_count"] == 4
    assert analysis["exit_reason_breakdown"]["stop"]["trade_count"] == 3
    assert analysis["symbol_breakdown"]["NQH6"]["net_pnl_dollars"] == 26.0
    assert analysis["side_breakdown"]["short"]["trade_count"] == 1
    assert analysis["stopped_trade_excursion"]["trade_count"] == 3
    assert analysis["stopped_trade_excursion"]["average_mfe_points"] == 11 / 3
    assert analysis["stopped_trade_excursion"]["max_mfe_points"] == 7.0
    assert analysis["stopped_trade_excursion"]["average_mae_points"] == -6.0
    assert analysis["dollar_equivalents"]["MNQ"]["point_value"] == 2.0
    assert analysis["dollar_equivalents"]["MNQ"]["total_net_pnl_dollars"] == -4.0
    assert analysis["dollar_equivalents"]["MNQ"]["max_drawdown_dollars"] == -30.0
    assert analysis["dollar_equivalents"]["NQ"]["point_value"] == 20.0
    assert analysis["dollar_equivalents"]["NQ"]["total_net_pnl_dollars"] == 32.0
    assert analysis["dollar_equivalents"]["NQ"]["max_drawdown_dollars"] == -264.0
