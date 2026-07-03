from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
RTH_START = time(9, 30)
RTH_END = time(16, 0)
CME_DAY_START = time(18, 0)
RTH_OPEN_WINDOW_MINUTES = 15


def parse_timestamp_utc(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    Accepts a trailing ``Z``, an explicit offset, or a naive timestamp
    (treated as UTC, per the canonical bar contract).
    """
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class SessionInfo:
    timestamp_et: datetime
    calendar_date_et: date
    session_date: date
    is_rth: bool
    is_rth_open_window: bool
    minutes_from_midnight_et: int


def classify_timestamp(raw_timestamp_utc: str) -> SessionInfo:
    """Classify a canonical bar timestamp against the CME/ET session model.

    - ``session_date`` is the CME trading day: it rolls forward at 18:00 ET,
      so Sunday 18:00 ET belongs to Monday's session.
    - ``is_rth`` is true for weekday bars in [09:30, 16:00) ET.
    - ``is_rth_open_window`` is true for the first 15 minutes of RTH, where
      entry slippage is elevated.
    """
    timestamp_et = parse_timestamp_utc(raw_timestamp_utc).astimezone(EASTERN)
    calendar_date_et = timestamp_et.date()
    local_time = timestamp_et.time()

    if local_time >= CME_DAY_START:
        session_date = calendar_date_et + timedelta(days=1)
    else:
        session_date = calendar_date_et

    is_weekday = timestamp_et.weekday() < 5
    is_rth = is_weekday and RTH_START <= local_time < RTH_END
    rth_open_end = time(RTH_START.hour, RTH_START.minute + RTH_OPEN_WINDOW_MINUTES)
    is_rth_open_window = is_rth and local_time < rth_open_end

    return SessionInfo(
        timestamp_et=timestamp_et,
        calendar_date_et=calendar_date_et,
        session_date=session_date,
        is_rth=is_rth,
        is_rth_open_window=is_rth_open_window,
        minutes_from_midnight_et=timestamp_et.hour * 60 + timestamp_et.minute,
    )


def minutes_of(value: time) -> int:
    return value.hour * 60 + value.minute
