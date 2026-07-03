from datetime import date

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
