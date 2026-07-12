"""US equity-index RTH calendar used by NQ/MNQ simulation and live gates.

The strategy treats 09:30-16:00 ET as its regular session. This calendar
therefore follows the US cash-equity holiday and scheduled 13:00 ET early-
close convention that governs that research window. Unscheduled closures must
be added explicitly and covered by a fixture before affected data is promoted.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

REGULAR_CLOSE_MINUTES_ET = 16 * 60
EARLY_CLOSE_MINUTES_ET = 13 * 60


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
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


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


@lru_cache(maxsize=None)
def full_holidays(year: int) -> frozenset[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),       # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),       # Presidents Day
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),         # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),       # Labor Day
        _nth_weekday(year, 11, 3, 4),      # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))
    # A Jan 1 Saturday is observed on Dec 31 of the prior year.
    next_new_year = _observed(date(year + 1, 1, 1))
    if next_new_year.year == year:
        holidays.add(next_new_year)
    return frozenset(holidays)


@lru_cache(maxsize=None)
def early_closes(year: int) -> frozenset[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {thanksgiving + timedelta(days=1)}

    july_fourth = date(year, 7, 4)
    july_third = date(year, 7, 3)
    if july_fourth.weekday() in (1, 2, 3, 4):
        candidates.add(july_third)

    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() < 4:
        candidates.add(christmas_eve)

    return frozenset(
        day for day in candidates
        if day.weekday() < 5 and day not in full_holidays(year)
    )


def rth_close_minutes_et(session_date: date) -> int | None:
    if session_date.weekday() >= 5 or session_date in full_holidays(session_date.year):
        return None
    if session_date in early_closes(session_date.year):
        return EARLY_CLOSE_MINUTES_ET
    return REGULAR_CLOSE_MINUTES_ET


def flatten_minutes_et(session_date: date, configured_minutes: int) -> int:
    close = rth_close_minutes_et(session_date)
    if close is None:
        return 0
    return min(configured_minutes, close - 1)
