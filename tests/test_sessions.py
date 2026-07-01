from full_python.data.sessions import filter_bars_by_session, is_rth_bar
from full_python.models import MarketBar


def test_is_rth_bar_uses_new_york_regular_session_boundaries() -> None:
    assert not is_rth_bar("2026-06-30T13:29:00Z")
    assert is_rth_bar("2026-06-30T13:30:00Z")
    assert is_rth_bar("2026-06-30T19:59:00Z")
    assert not is_rth_bar("2026-06-30T20:00:00Z")


def test_is_rth_bar_handles_new_york_dst_offset() -> None:
    assert not is_rth_bar("2026-01-15T14:29:00Z")
    assert is_rth_bar("2026-01-15T14:30:00Z")
    assert is_rth_bar("2026-01-15T20:59:00Z")
    assert not is_rth_bar("2026-01-15T21:00:00Z")


def test_filter_bars_by_session_keeps_all_or_rth() -> None:
    bars = [
        MarketBar("2026-06-30T13:29:00Z", "NQU2026", 1, 1, 1, 1, 1),
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 2, 2, 2, 2, 2),
        MarketBar("2026-06-30T20:00:00Z", "NQU2026", 3, 3, 3, 3, 3),
    ]

    assert [bar.close for bar in filter_bars_by_session(bars, "all")] == [1, 2, 3]
    assert [bar.close for bar in filter_bars_by_session(bars, "rth")] == [2]
