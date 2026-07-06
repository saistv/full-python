from datetime import date, datetime, timezone

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


def _source(items, window=RTH_WINDOW, position=FLAT, clock=None):
    return LiveBarSource(ScriptedFeed(items), clock or FakeClock(CLOCK.now()),
                         AUTH, window, position)


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
