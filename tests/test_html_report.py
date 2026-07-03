from full_python.models import Trade
from full_python.reporting.html_report import render_html_report


def _trade(net_pnl: float, quantity: int = 1) -> Trade:
    return Trade(
        symbol="NQ", side="long", quantity=quantity,
        entry_timestamp_utc="2026-06-30T13:33:00Z", entry_price=100.0,
        exit_timestamp_utc="2026-06-30T13:40:00Z", exit_price=110.0,
        exit_reason="stop" if net_pnl < 0 else "atf_flip", stop_price=80.0,
        gross_points=10.0, gross_pnl=net_pnl + 10.0, commission=10.0,
        net_pnl=net_pnl, mfe_points=1.0, mae_points=1.0, session_date="2026-06-30",
    )


REPORT = {
    "run_id": "abc123-def456-789",
    "data": {
        "dataset_name": "nq_test",
        "start_timestamp_utc": "2026-06-01T00:00:00Z",
        "end_timestamp_utc": "2026-06-30T20:59:00Z",
    },
    "strategy": {"name": "adaptive_trend_v66_am", "parameter_hash": "cafe" * 16},
    "simulation": {"parameter_hash": "beef" * 16},
    "survivability": {"net_pnl": 1234.0, "max_drawdown": -650.0},
    "daily": {
        "sharpe_annualized": 2.43, "profitable_day_rate": 0.55,
        "best_day_pnl": 900.0, "worst_day_pnl": -670.0,
        "max_time_underwater_days": 12,
    },
    "monthly": {"2026-06": {"net_pnl": 1234.0, "days_with_trades": 4}},
    "exit_reasons": {"stop": 2, "atf_flip": 2},
    "ambiguous_exits": 0,
}

TRADES = [_trade(500.0), _trade(-650.0), _trade(1400.0, quantity=2), _trade(-16.0)]
DAILY = [
    ("2026-06-29", 0.0, 0.0),
    ("2026-06-30", 1234.0, 1234.0),
]


def test_report_is_self_contained_html() -> None:
    html = render_html_report(REPORT, TRADES, DAILY, {"wings_fail": 40, "cooldown": 7})

    assert html.startswith("<!DOCTYPE html>")
    # No external assets of any kind.
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html
    # Headline stats present.
    assert "$1,234" in html
    assert "-$650" in html
    assert "2.43" in html
    # Equity curve and histogram render as inline SVG.
    assert html.count("<svg") >= 2
    # Sizing table reflects the 2-contract trade.
    assert "2 contracts" in html
    # Rejection gates section present and ordered by count.
    assert html.index("wings_fail") < html.index("cooldown")


def test_report_handles_empty_run() -> None:
    empty_report = {**REPORT, "survivability": {"net_pnl": 0.0, "max_drawdown": 0.0},
                    "monthly": {}, "exit_reasons": {}}
    html = render_html_report(empty_report, [], [], {})

    assert html.startswith("<!DOCTYPE html>")
    assert "No trades" in html
    assert "No daily data" in html
