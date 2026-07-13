"""The calendar is pinned to the exchange, not to a rule we believe in.

``tests/fixtures/cme_equity_rth_close.json`` is derived from five years of
Databento GLBX NQ front-month bars: for every weekday, the last one-minute bar
inside [09:30, 16:00) ET plus one minute, or null when the session has no RTH
bars at all. It is the exchange's own record of when CME equity-index futures
stopped trading, and it is the authority these tests enforce.

The distinction that matters: CME equity-index futures are NOT the US cash
equity market. On MLK, Presidents Day, Memorial Day, Juneteenth, Independence
Day, Labor Day and Thanksgiving the cash market is shut but NQ trades an
abbreviated 09:30-13:00 ET session -- the strategy's entire 09:30-10:00 entry
window is open, with real volume. Only Good Friday, Christmas and New Year are
full closures.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from full_python.data.exchange_calendar import (
    EARLY_CLOSE_MINUTES_ET,
    HOLIDAY_CLOSE_MINUTES_ET,
    REGULAR_CLOSE_MINUTES_ET,
    rth_close_minutes_et,
)

FIXTURE = Path(__file__).parent / "fixtures" / "cme_equity_rth_close.json"


def _expected() -> dict[date, int | None]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {
        date.fromisoformat(day): close
        for day, close in payload["close_minutes_et"].items()
    }


def test_calendar_matches_five_years_of_exchange_data_exactly() -> None:
    mismatches = []
    for day, expected in sorted(_expected().items()):
        actual = rth_close_minutes_et(day)
        if actual != expected:
            mismatches.append(
                f"{day} ({day.strftime('%a')}): calendar={actual} exchange={expected}"
            )
    assert not mismatches, "calendar disagrees with the exchange on:\n" + "\n".join(
        mismatches[:40]
    )


def test_the_seven_abbreviated_holidays_are_open_for_the_entry_window() -> None:
    # Every one of these trades 09:30-13:00 ET. The entry window (09:30-10:00)
    # is fully open. Declaring them closed deletes real, TV-matched trades.
    for day in (
        date(2026, 1, 19),   # Martin Luther King Jr. Day
        date(2026, 2, 16),   # Presidents Day
        date(2026, 5, 25),   # Memorial Day
        date(2026, 6, 19),   # Juneteenth
        date(2025, 7, 4),    # Independence Day
        date(2025, 9, 1),    # Labor Day
        date(2025, 11, 27),  # Thanksgiving
    ):
        assert rth_close_minutes_et(day) == HOLIDAY_CLOSE_MINUTES_ET, day


def test_only_good_friday_christmas_and_new_year_are_full_closures() -> None:
    for day in (
        date(2026, 4, 3),    # Good Friday
        date(2025, 12, 25),  # Christmas Day
        date(2026, 1, 1),    # New Year's Day
    ):
        assert rth_close_minutes_et(day) is None, day


def test_scheduled_early_closes_end_at_1315_not_1300() -> None:
    for day in (
        date(2025, 11, 28),  # day after Thanksgiving
        date(2025, 12, 24),  # Christmas Eve
        date(2025, 7, 3),    # day before Independence Day
    ):
        assert rth_close_minutes_et(day) == EARLY_CLOSE_MINUTES_ET, day


def test_new_year_on_a_saturday_is_not_observed_but_christmas_is() -> None:
    # Exchange record: 2021-12-31 (Fri, New Year's Day fell on Sat) traded a full
    # regular session, while 2021-12-24 (Fri, Christmas fell on Sat) was closed.
    # The naive "Saturday -> observe Friday" rule gets the first one wrong.
    assert rth_close_minutes_et(date(2021, 12, 31)) == REGULAR_CLOSE_MINUTES_ET
    assert rth_close_minutes_et(date(2021, 12, 24)) is None


def test_sunday_holidays_are_observed_on_the_following_monday() -> None:
    assert rth_close_minutes_et(date(2023, 1, 2)) is None                    # New Year (Sun)
    assert rth_close_minutes_et(date(2022, 12, 26)) is None                  # Christmas (Sun)
    assert rth_close_minutes_et(date(2021, 7, 5)) == HOLIDAY_CLOSE_MINUTES_ET   # July 4 (Sun)
    assert rth_close_minutes_et(date(2022, 6, 20)) == HOLIDAY_CLOSE_MINUTES_ET  # Juneteenth (Sun)


def test_juneteenth_is_not_observed_before_2022() -> None:
    # CME did not run a holiday schedule for the first federal Juneteenth (2021).
    assert rth_close_minutes_et(date(2021, 6, 18)) == REGULAR_CLOSE_MINUTES_ET


def test_ad_hoc_closure_national_day_of_mourning() -> None:
    # 2025-01-09, President Carter. Not derivable from any rule; must be listed.
    assert rth_close_minutes_et(date(2025, 1, 9)) is None


@pytest.mark.parametrize("day", [date(2026, 3, 10), date(2025, 6, 11)])
def test_ordinary_weekdays_close_at_1600(day: date) -> None:
    assert rth_close_minutes_et(day) == REGULAR_CLOSE_MINUTES_ET


def test_weekends_have_no_rth(  ) -> None:
    assert rth_close_minutes_et(date(2026, 3, 14)) is None  # Saturday
    assert rth_close_minutes_et(date(2026, 3, 15)) is None  # Sunday
