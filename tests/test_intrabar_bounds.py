import pytest

from full_python.models import MarketBar, Trade
from full_python.research.intrabar_bounds import build_intrabar_bounds_report


def _bar(timestamp: str, high: float, low: float) -> MarketBar:
    return MarketBar(
        timestamp_utc=timestamp,
        symbol="NQ1!",
        open=100.0,
        high=high,
        low=low,
        close=101.0,
        volume=100.0,
    )


def _trade(
    *, side: str, entry_timestamp: str, exit_timestamp: str,
    entry_price: float, mfe: float, ambiguous: bool = True,
) -> Trade:
    return Trade(
        symbol="NQ1!",
        side=side,
        quantity=1,
        entry_timestamp_utc=entry_timestamp,
        entry_price=entry_price,
        exit_timestamp_utc=exit_timestamp,
        exit_price=95.0,
        exit_reason="stop",
        stop_price=95.5,
        gross_points=-4.5,
        gross_pnl=-90.0,
        commission=10.0,
        net_pnl=-100.0,
        mfe_points=mfe,
        mae_points=4.5,
        session_date="2026-06-30",
        ambiguous_exit=ambiguous,
    )


def test_intrabar_bounds_measure_long_and_short_uncertainty() -> None:
    long_time = "2026-06-30T13:31:00Z"
    short_time = "2026-06-30T13:32:00Z"
    trades = [
        _trade(
            side="long", entry_timestamp=long_time, exit_timestamp=long_time,
            entry_price=100.0, mfe=5.0,
        ),
        _trade(
            side="short", entry_timestamp="2026-06-30T13:30:00Z",
            exit_timestamp=short_time, entry_price=100.0, mfe=4.0,
        ),
    ]
    bars = [
        _bar(long_time, high=120.0, low=94.0),
        _bar(short_time, high=106.0, low=82.0),
    ]

    report = build_intrabar_bounds_report(trades, bars, thresholds=(5.0, 10.0, 20.0))

    assert report.trade_count == 2
    assert report.stop_trade_count == 2
    assert report.entry_minute_stop_count == 1
    assert report.entry_minute_stop_net_pnl == -100.0
    assert report.ambiguous_exit_count == 2
    assert report.confirmed_mfe_total == 9.0
    assert report.mfe_upper_bound_total == 38.0
    assert report.mfe_uncertainty_total == 29.0
    assert report.mfe_uncertainty_median == 14.5
    assert report.mfe_uncertainty_max == 15.0
    assert report.unresolved_threshold_counts == {
        "5": 1,
        "10": 2,
        "20": 1,
    }
    assert report.pnl_path_uncertain_trade_count == 0


def test_nonambiguous_stop_does_not_create_an_mfe_interval() -> None:
    timestamp = "2026-06-30T13:31:00Z"
    trade = _trade(
        side="long", entry_timestamp=timestamp, exit_timestamp=timestamp,
        entry_price=100.0, mfe=0.0, ambiguous=False,
    )

    report = build_intrabar_bounds_report([trade], [_bar(timestamp, 100.0, 94.0)])

    assert report.entry_minute_stop_count == 1
    assert report.ambiguous_exit_count == 0
    assert report.mfe_uncertainty_total == 0.0


def test_missing_bar_for_ambiguous_exit_fails_closed() -> None:
    timestamp = "2026-06-30T13:31:00Z"
    trade = _trade(
        side="long", entry_timestamp=timestamp, exit_timestamp=timestamp,
        entry_price=100.0, mfe=0.0,
    )

    with pytest.raises(ValueError, match="missing exit bar"):
        build_intrabar_bounds_report([trade], [])
