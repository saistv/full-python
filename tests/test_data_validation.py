from full_python.data.validation import validate_bars
from full_python.models import MarketBar


def _bar(timestamp: str, *, symbol: str = "NQU2026", open_: float = 100.0, high: float = 101.0, low: float = 99.0, close: float = 100.5, volume: float = 10.0) -> MarketBar:
    return MarketBar(
        timestamp_utc=timestamp,
        symbol=symbol,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def test_clean_bars_pass() -> None:
    report = validate_bars(
        [
            _bar("2026-06-30T13:30:00Z"),
            _bar("2026-06-30T13:31:00Z"),
            _bar("2026-06-30T13:32:00Z"),
        ]
    )

    assert report.is_structurally_clean
    assert report.bar_count == 3
    assert report.intra_session_gap_count == 0
    assert report.symbols == ("NQU2026",)


def test_backward_and_duplicate_timestamps_are_structural() -> None:
    report = validate_bars(
        [
            _bar("2026-06-30T13:31:00Z"),
            _bar("2026-06-30T13:30:00Z"),
            _bar("2026-06-30T13:30:00Z"),
        ]
    )

    assert not report.is_structurally_clean
    assert report.issue_counts["non_monotonic_timestamp"] == 1
    assert report.issue_counts["duplicate_timestamp"] == 1


def test_malformed_ohlc_is_structural() -> None:
    report = validate_bars(
        [
            _bar("2026-06-30T13:30:00Z", high=98.0),
            _bar("2026-06-30T13:31:00Z", open_=-5.0),
        ]
    )

    assert report.issue_counts["invalid_ohlc"] == 1
    assert report.issue_counts["non_positive_price"] == 1


def test_rth_gap_is_structural() -> None:
    report = validate_bars(
        [
            _bar("2026-06-30T13:30:00Z"),
            _bar("2026-06-30T13:36:00Z"),
        ]
    )

    assert not report.is_structurally_clean
    assert report.issue_counts["rth_gap"] == 1
    assert report.intra_session_gap_count == 1
    assert report.max_intra_session_gap_minutes == 6.0


def test_overnight_gap_is_counted_but_not_structural() -> None:
    report = validate_bars(
        [
            _bar("2026-06-30T06:00:00Z"),
            _bar("2026-06-30T06:06:00Z"),
        ]
    )

    assert report.is_structurally_clean
    assert report.intra_session_gap_count == 1
    assert "rth_gap" not in report.issue_counts


def test_overnight_session_boundary_is_not_a_gap() -> None:
    report = validate_bars(
        [
            _bar("2026-06-30T19:59:00Z"),
            _bar("2026-06-30T22:00:00Z"),
        ]
    )

    assert report.intra_session_gap_count == 0


def test_mixed_symbols_flagged() -> None:
    report = validate_bars(
        [
            _bar("2026-06-30T13:30:00Z", symbol="NQU2026"),
            _bar("2026-06-30T13:31:00Z", symbol="MNQU2026"),
        ]
    )

    assert report.issue_counts["symbol_mix"] == 1
    assert not report.is_structurally_clean
