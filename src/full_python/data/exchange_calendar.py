"""CME equity-index (NQ/MNQ) RTH calendar.

**This is a futures calendar, not a cash-equity calendar.** The distinction is
the whole point of this module, and getting it wrong deletes real trades:

- On MLK Day, Presidents Day, Memorial Day, Juneteenth, Independence Day, Labor
  Day and Thanksgiving the US cash equity market is shut, but CME equity-index
  futures trade an **abbreviated 09:30-13:00 ET session**. The strategy's entire
  09:30-10:00 entry window is open on every one of them, with real volume. These
  days are tradeable and were traded by the TradingView reference.
- Only **Good Friday, Christmas Day and New Year's Day** are full closures.
- Three scheduled sessions end at **13:15 ET**: the day after Thanksgiving,
  Christmas Eve, and the day before Independence Day.

Every rule below is pinned against five years of Databento GLBX NQ front-month
bars by ``tests/test_exchange_calendar.py`` (fixture:
``tests/fixtures/cme_equity_rth_close.json`` -- 1,379 weekdays, the exchange's
own record). Two observance rules in particular are counter-intuitive and were
derived from that data rather than assumed:

- New Year's Day falling on a **Saturday is not observed at all** (2021-12-31
  traded a full regular session), while Christmas falling on a Saturday **is**
  observed on the preceding Friday (2021-12-24 was closed).
- CME did not run a holiday schedule for the first federal Juneteenth (2021).

Unscheduled closures cannot be derived from any rule and must be listed in
``AD_HOC_FULL_CLOSURES`` with a fixture entry.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

REGULAR_CLOSE_MINUTES_ET = 16 * 60       # 16:00 -- ordinary session
EARLY_CLOSE_MINUTES_ET = 13 * 60 + 15    # 13:15 -- scheduled early close
HOLIDAY_CLOSE_MINUTES_ET = 13 * 60       # 13:00 -- abbreviated holiday session

# Unscheduled full closures. Not derivable; each needs a fixture entry.
AD_HOC_FULL_CLOSURES = frozenset({
    date(2025, 1, 9),  # National Day of Mourning, President Carter
})


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """Saturday -> preceding Friday, Sunday -> following Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _observed_no_saturday_rollback(day: date) -> date | None:
    """New Year's Day only: a Saturday New Year is simply not observed.

    Verified against the exchange: New Year's Day 2022 fell on a Saturday and
    both 2021-12-31 and 2022-01-03 traded ordinary full sessions.
    """
    if day.weekday() == 5:
        return None
    return _observed(day)


def _easter_sunday(year: int) -> date:
    # Anonymous Gregorian algorithm.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def good_friday(year: int) -> date:
    return _easter_sunday(year) - timedelta(days=2)


def independence_day_observed(year: int) -> date:
    return _observed(date(year, 7, 4))


@lru_cache(maxsize=None)
def full_closures(year: int) -> frozenset:
    """Days with no RTH session at all."""
    days = {good_friday(year), _observed(date(year, 12, 25))}
    new_year = _observed_no_saturday_rollback(date(year, 1, 1))
    if new_year is not None:
        days.add(new_year)
    return frozenset(days)


@lru_cache(maxsize=None)
def abbreviated_sessions(year: int) -> frozenset:
    """Holiday sessions that trade 09:30-13:00 ET.

    The cash equity market is closed; NQ is not. The entry window is open.
    """
    days = {
        _nth_weekday(year, 1, 0, 3),          # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),          # Presidents Day
        _last_weekday(year, 5, 0),            # Memorial Day
        independence_day_observed(year),
        _nth_weekday(year, 9, 0, 1),          # Labor Day
        _nth_weekday(year, 11, 3, 4),         # Thanksgiving
    }
    if year >= 2022:                          # CME did not observe Juneteenth in 2021
        days.add(_observed(date(year, 6, 19)))
    return frozenset(days)


@lru_cache(maxsize=None)
def early_closes(year: int) -> frozenset:
    """Scheduled 13:15 ET closes: the sessions before a market holiday.

    A day that is itself a holiday (full closure or abbreviated session) is not
    an early close -- the holiday schedule wins.
    """
    candidates = {
        _nth_weekday(year, 11, 3, 4) + timedelta(days=1),  # day after Thanksgiving
        date(year, 7, 3),                                  # day before Independence Day
        date(year, 12, 24),                                # Christmas Eve
    }
    holidays = full_closures(year) | abbreviated_sessions(year)
    return frozenset(
        day for day in candidates
        if day.weekday() < 5 and day not in holidays
    )


def rth_close_minutes_et(session_date: date):
    """Minutes from ET midnight at which the RTH session ends, or None if closed."""
    if session_date.weekday() >= 5:
        return None
    if session_date in AD_HOC_FULL_CLOSURES:
        return None
    year = session_date.year
    if session_date in full_closures(year):
        return None
    if session_date in abbreviated_sessions(year):
        return HOLIDAY_CLOSE_MINUTES_ET
    if session_date in early_closes(year):
        return EARLY_CLOSE_MINUTES_ET
    return REGULAR_CLOSE_MINUTES_ET


def flatten_minutes_et(session_date: date, configured_minutes: int) -> int:
    """The backstop flatten minute, never later than one minute before the close."""
    close = rth_close_minutes_et(session_date)
    if close is None:
        return 0
    return min(configured_minutes, close - 1)
