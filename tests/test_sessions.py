from datetime import date

from full_python.data.exchange_calendar import rth_close_minutes_et
from full_python.data.sessions import classify_timestamp, parse_timestamp_utc


def test_parse_accepts_z_suffix_offset_and_naive_utc() -> None:
    assert parse_timestamp_utc("2026-06-30T13:30:00Z").hour == 13
    assert parse_timestamp_utc("2026-06-30T09:30:00-04:00").hour == 13
    assert parse_timestamp_utc("2026-06-30T13:30:00").hour == 13


def test_summer_rth_open_maps_to_930_eastern() -> None:
    info = classify_timestamp("2026-06-30T13:30:00Z")

    assert info.timestamp_et.hour == 9
    assert info.timestamp_et.minute == 30
    assert info.is_rth
    assert info.is_rth_open_window
    assert info.session_date == date(2026, 6, 30)


def test_winter_uses_est_offset() -> None:
    info = classify_timestamp("2026-01-15T14:30:00Z")

    assert info.timestamp_et.hour == 9
    assert info.timestamp_et.minute == 30
    assert info.is_rth


def test_rth_boundaries() -> None:
    assert not classify_timestamp("2026-06-30T13:29:00Z").is_rth
    assert classify_timestamp("2026-06-30T19:59:00Z").is_rth
    assert not classify_timestamp("2026-06-30T20:00:00Z").is_rth
    assert not classify_timestamp("2026-06-30T13:45:00Z").is_rth_open_window
    assert classify_timestamp("2026-06-30T13:44:00Z").is_rth_open_window


def test_cme_session_rolls_forward_at_1800_eastern() -> None:
    before_roll = classify_timestamp("2026-06-30T21:59:00Z")
    after_roll = classify_timestamp("2026-06-30T22:00:00Z")

    assert before_roll.session_date == date(2026, 6, 30)
    assert after_roll.session_date == date(2026, 7, 1)


def test_sunday_open_belongs_to_monday_session_and_is_not_rth() -> None:
    info = classify_timestamp("2026-07-05T22:30:00Z")

    assert info.timestamp_et.weekday() == 6
    assert info.session_date == date(2026, 7, 6)
    assert not info.is_rth


def test_saturday_is_never_rth() -> None:
    assert not classify_timestamp("2026-07-04T14:00:00Z").is_rth


def test_only_full_closures_are_not_rth() -> None:
    # Good Friday is a genuine CME equity-index closure: zero RTH bars.
    assert not classify_timestamp("2026-04-03T13:30:00Z").is_rth
    assert rth_close_minutes_et(date(2026, 4, 3)) is None


def test_thanksgiving_trades_an_abbreviated_session_and_is_rth() -> None:
    # The cash market is shut; NQ trades 09:30-13:00 ET. The exchange record
    # shows 210 RTH bars and real volume in the 09:30-10:00 entry window, and
    # the TradingView reference took a trade here. It is RTH.
    assert classify_timestamp("2025-11-27T14:30:00Z").is_rth       # 09:30 ET
    assert classify_timestamp("2025-11-27T17:59:00Z").is_rth       # 12:59 ET
    assert not classify_timestamp("2025-11-27T18:00:00Z").is_rth   # 13:00 ET -- closed
    assert rth_close_minutes_et(date(2025, 11, 27)) == 13 * 60


def test_scheduled_early_close_ends_rth_at_1315_et() -> None:
    # Day after Thanksgiving 2025: futures run 15 minutes past the cash close.
    # Exchange record: last bar 13:14 ET.
    assert classify_timestamp("2025-11-28T18:00:00Z").is_rth       # 13:00 ET -- still open
    assert classify_timestamp("2025-11-28T18:14:00Z").is_rth       # 13:14 ET
    assert not classify_timestamp("2025-11-28T18:15:00Z").is_rth   # 13:15 ET -- closed
    assert rth_close_minutes_et(date(2025, 11, 28)) == 13 * 60 + 15


def test_observed_holidays_and_christmas_eve_rules() -> None:
    # 2026-07-04 falls on a Saturday, so Independence Day is observed Friday
    # 2026-07-03 -- an abbreviated holiday session, following the observed-
    # holiday pattern verified in the fixture at 2021-07-05 (Sunday -> Monday).
    # Beyond the fixture's data range; flagged as a rule extrapolation.
    assert rth_close_minutes_et(date(2026, 7, 3)) == 13 * 60
    # Christmas 2026 is a Friday, so Dec 24 is an ordinary early close at 13:15.
    assert rth_close_minutes_et(date(2026, 12, 24)) == 13 * 60 + 15
    # Christmas 2021 fell on a Saturday: observed Friday Dec 24, fully closed.
    assert rth_close_minutes_et(date(2021, 12, 24)) is None
