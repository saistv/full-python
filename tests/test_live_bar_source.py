from datetime import date, datetime, timedelta, timezone

import pytest

from full_python.livedata.contract_authority import ContractAuthority
from full_python.livedata.errors import DataIntegrityError, DataOutageError
from full_python.livedata.feed import VendorBar
from full_python.livedata.live_bar_source import ActiveWindow, LiveBarSource


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now
    def now(self) -> datetime:
        return self._now
    def set(self, now: datetime) -> None:
        self._now = now


class ScriptedFeed:
    """Returns queued items (VendorBar or None) in order; ignores timeout."""
    def __init__(self, items) -> None:
        self._items = list(items)
        self._i = 0
    def next_bar(self, timeout_seconds: float):
        if self._i >= len(self._items):
            return None
        item = self._items[self._i]
        self._i += 1
        return item


class AdvancingFeed(ScriptedFeed):
    """Models a real timeout by advancing the injected wall clock."""

    def __init__(self, items, clock) -> None:
        super().__init__(items)
        self._clock = clock
        self.calls = 0

    def next_bar(self, timeout_seconds: float):
        self.calls += 1
        item = super().next_bar(timeout_seconds)
        if item is None:
            self._clock.set(
                self._clock.now() + timedelta(seconds=timeout_seconds)
            )
        return item


# Front contract for a Nov-2025 session is NQZ5 (validated roll logic).
AUTH = ContractAuthority(root="NQ")
FRONT_NOV_2025 = AUTH.front_contract(date(2025, 11, 3))  # concrete, not hardcoded


def _vbar(ts, symbol=FRONT_NOV_2025, o=100.0, h=101.0, l=99.0, c=100.5, v=5.0):
    return VendorBar(symbol=symbol, timestamp_utc=ts, open=o, high=h, low=l, close=c, volume=v)


# RTH minutes: 14:31Z .. in Nov EST is 09:31 ET. entry window 570-600 ET.
RTH_WINDOW = ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=16 * 60)
FLAT = lambda: False
IN_POSITION = lambda: True
CLOCK = FakeClock(datetime(2025, 11, 3, 15, 0, tzinfo=timezone.utc))


def _source(
    items,
    window=RTH_WINDOW,
    position=FLAT,
    clock=None,
    session_end_minutes_et=None,
):
    return LiveBarSource(ScriptedFeed(items), clock or FakeClock(CLOCK.now()),
                         AUTH, window, position,
                         session_end_minutes_et=session_end_minutes_et)


def test_active_window_contains():
    w = ActiveWindow(start_minutes_et=570, end_minutes_et=600)
    assert not w.contains(569)
    assert w.contains(570)
    assert w.contains(599)
    assert not w.contains(600)


def test_normalizes_vendor_bar_to_marketbar_with_front_symbol():
    src = _source([_vbar("2025-11-03T14:31:00Z")])
    bar = next(iter(src))
    assert bar.timestamp_utc == "2025-11-03T14:31:00Z"
    assert bar.symbol == FRONT_NOV_2025
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (100.0, 101.0, 99.0, 100.5, 5.0)


def test_wrong_contract_symbol_raises_integrity():
    src = _source([_vbar("2025-11-03T14:31:00Z", symbol="NQH6")])
    with pytest.raises(DataIntegrityError):
        next(iter(src))


@pytest.mark.parametrize(
    "bar",
    [
        _vbar("2025-11-03T14:31:00Z", h=float("inf")),
        _vbar("2025-11-03T14:31:00Z", v=float("nan")),
    ],
)
def test_nonfinite_vendor_bar_raises_integrity(bar):
    with pytest.raises(DataIntegrityError, match="non_finite"):
        next(iter(_source([bar])))


def test_non_monotonic_timestamp_raises_integrity():
    src = _source([_vbar("2025-11-03T14:32:00Z"), _vbar("2025-11-03T14:32:00Z")])
    it = iter(src)
    assert next(it).timestamp_utc == "2025-11-03T14:32:00Z"
    with pytest.raises(DataIntegrityError):
        next(it)  # duplicate timestamp


def test_armed_timeout_raises_outage():
    # one bar, then the feed dries up; a position is open -> armed -> outage
    src = _source([_vbar("2025-11-03T14:32:00Z")], position=IN_POSITION)
    it = iter(src)
    assert next(it).timestamp_utc == "2025-11-03T14:32:00Z"
    with pytest.raises(DataOutageError):
        next(it)  # feed returns None; armed by open position


def test_cold_start_inside_active_window_halts_on_first_timeout():
    clock = FakeClock(datetime(2025, 11, 3, 14, 35, 30, tzinfo=timezone.utc))
    window = ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=10 * 60)
    src = _source(
        [None, _vbar("2025-11-03T14:35:00Z")],
        window=window,
        position=FLAT,
        clock=clock,
    )

    with pytest.raises(DataOutageError, match="cold-start"):
        next(iter(src))


def test_late_first_bar_inside_active_window_misses_startup_deadline():
    clock = FakeClock(datetime(2025, 11, 3, 14, 35, 30, tzinfo=timezone.utc))

    class LateFirstBarFeed:
        def next_bar(self, timeout_seconds: float):
            clock.set(clock.now() + timedelta(seconds=timeout_seconds + 1))
            return _vbar("2025-11-03T14:35:00Z")

    src = LiveBarSource(
        LateFirstBarFeed(),
        clock,
        AUTH,
        ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=10 * 60),
        FLAT,
    )

    with pytest.raises(DataOutageError, match="after cold-start deadline"):
        next(iter(src))


def test_cold_start_before_active_window_can_accept_a_late_first_bar():
    clock = FakeClock(datetime(2025, 11, 3, 14, 29, 30, tzinfo=timezone.utc))
    feed = AdvancingFeed(
        [None, _vbar("2025-11-03T14:30:00Z")],
        clock,
    )
    src = LiveBarSource(
        feed,
        clock,
        AUTH,
        ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=10 * 60),
        FLAT,
        session_end_minutes_et=16 * 60 + 5,
    )

    assert next(iter(src)).timestamp_utc == "2025-11-03T14:30:00Z"
    assert feed.calls == 2


def test_cold_start_after_active_window_stops_at_configured_session_end():
    clock = FakeClock(datetime(2025, 11, 3, 15, 5, 0, tzinfo=timezone.utc))
    feed = AdvancingFeed([None, None, None], clock)
    src = LiveBarSource(
        feed,
        clock,
        AUTH,
        ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=10 * 60),
        FLAT,
        session_end_minutes_et=10 * 60 + 7,
    )

    assert list(src) == []
    assert feed.calls == 2
    assert clock.now() == datetime(2025, 11, 3, 15, 7, 0, tzinfo=timezone.utc)


def test_cold_start_after_session_end_does_not_poll_feed():
    clock = FakeClock(datetime(2025, 11, 3, 15, 8, 0, tzinfo=timezone.utc))
    feed = AdvancingFeed([], clock)
    src = LiveBarSource(
        feed,
        clock,
        AUTH,
        ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=10 * 60),
        FLAT,
        session_end_minutes_et=10 * 60 + 7,
    )

    assert list(src) == []
    assert feed.calls == 0


def test_open_position_never_stops_quietly_at_session_end_without_data():
    clock = FakeClock(datetime(2025, 11, 3, 15, 8, 0, tzinfo=timezone.utc))
    src = _source(
        [None],
        position=IN_POSITION,
        clock=clock,
        session_end_minutes_et=10 * 60 + 7,
    )

    with pytest.raises(DataOutageError, match="cold-start"):
        next(iter(src))


def test_armed_interior_gap_raises_outage():
    # consecutive expectation broken: 14:32 then 14:34 (skips 14:33) while armed
    src = _source([_vbar("2025-11-03T14:32:00Z"), _vbar("2025-11-03T14:34:00Z")],
                  position=IN_POSITION)
    it = iter(src)
    assert next(it).timestamp_utc == "2025-11-03T14:32:00Z"
    with pytest.raises(DataOutageError):
        next(it)


def test_disarmed_timeout_advances_without_raising():
    # flat + outside the active window: a missing minute is normal (e.g. the
    # CME maintenance break). Feed gives one bar then dries up; no raise, and
    # a later bar is still delivered after the gap.
    # 03:00Z on a weekday is deep overnight ET -> outside RTH -> disarmed.
    src = _source(
        [_vbar("2025-11-03T03:00:00Z"), None, None, _vbar("2025-11-03T03:05:00Z")],
        position=FLAT,
    )
    it = iter(src)
    assert next(it).timestamp_utc == "2025-11-03T03:00:00Z"
    # the two Nones (missing 03:01, 03:02...) do not raise; the 03:05 bar arrives
    assert next(it).timestamp_utc == "2025-11-03T03:05:00Z"


def test_gap_starting_in_window_raises_even_if_next_bar_lands_after_close():
    # Regression for the arming-moment fix: flat, window 09:30-10:00 ET.
    # Last bar at 14:35Z (09:35 ET, in window); the feed drops bars and the
    # NEXT bar to actually arrive is 15:05Z (10:05 ET, just past the window).
    # The gap STARTED inside the window, so it must raise -- arming off the
    # arrival moment (10:05, out of window) would wrongly swallow it.
    win = ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=10 * 60)
    src = _source([_vbar("2025-11-03T14:35:00Z"), _vbar("2025-11-03T15:05:00Z")],
                  window=win, position=FLAT)
    it = iter(src)
    assert next(it).timestamp_utc == "2025-11-03T14:35:00Z"
    with pytest.raises(DataOutageError):
        next(it)


def test_window_is_malleable_not_hardcoded():
    # Same seed bar at 14:59Z (~09:59 ET) then feed dries up, flat.
    # Window A = 09:30-10:00 ET: 10:00 (the missing minute) is OUT -> disarmed -> no raise.
    # Window B = 10:00-11:00 ET: 10:00 is IN -> armed -> DataOutageError.
    seed = "2025-11-03T14:59:00Z"
    win_a = ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=10 * 60)
    win_b = ActiveWindow(start_minutes_et=10 * 60, end_minutes_et=11 * 60)

    src_a = _source([_vbar(seed)], window=win_a, position=FLAT)
    it_a = iter(src_a)
    assert next(it_a).timestamp_utc == seed
    # disarmed: advances, no raise -- pull once more with feed empty; still no raise
    # (we assert by confirming StopIteration is NOT how it fails: it must not raise
    # DataOutageError; the disarmed path loops, so we cap the attempt with a 2nd bar)
    src_a2 = _source([_vbar(seed), _vbar("2025-11-03T15:10:00Z")], window=win_a, position=FLAT)
    it_a2 = iter(src_a2)
    assert next(it_a2).timestamp_utc == seed
    assert next(it_a2).timestamp_utc == "2025-11-03T15:10:00Z"  # no outage across the gap

    src_b = _source([_vbar(seed)], window=win_b, position=FLAT)
    it_b = iter(src_b)
    assert next(it_b).timestamp_utc == seed
    with pytest.raises(DataOutageError):
        next(it_b)  # same gap, but armed by the wider window


def test_full_closure_gap_is_not_armed_by_wall_clock_window_when_flat():
    # Good Friday is a genuine full closure: no RTH session exists, so a gap
    # inside the numeric 09:30-10:00 window must not alarm a flat observer.
    closed_auth = ContractAuthority(root="NQ")
    closed_symbol = closed_auth.front_contract(date(2026, 4, 3))
    src = LiveBarSource(
        ScriptedFeed([
            _vbar("2026-04-03T13:35:00Z", symbol=closed_symbol),
            _vbar("2026-04-03T13:40:00Z", symbol=closed_symbol),
        ]),
        FakeClock(CLOCK.now()),
        closed_auth,
        ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=10 * 60),
        FLAT,
    )
    it = iter(src)
    assert next(it).timestamp_utc == "2026-04-03T13:35:00Z"
    assert next(it).timestamp_utc == "2026-04-03T13:40:00Z"


def test_abbreviated_holiday_session_IS_armed_inside_the_entry_window():
    # Thanksgiving trades 09:30-13:00 ET and the strategy trades it, so a gap in
    # the entry window is a real data outage and must halt -- the previous
    # cash-equity calendar silently disarmed the guard on exactly these days.
    holiday_auth = ContractAuthority(root="NQ")
    holiday_symbol = holiday_auth.front_contract(date(2025, 11, 27))
    src = LiveBarSource(
        ScriptedFeed([
            _vbar("2025-11-27T14:35:00Z", symbol=holiday_symbol),
            _vbar("2025-11-27T14:40:00Z", symbol=holiday_symbol),  # 5-minute hole
        ]),
        FakeClock(CLOCK.now()),
        holiday_auth,
        ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=10 * 60),
        FLAT,
    )
    it = iter(src)
    assert next(it).timestamp_utc == "2025-11-27T14:35:00Z"
    with pytest.raises(DataOutageError):
        next(it)
