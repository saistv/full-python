from __future__ import annotations

from datetime import datetime, time
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from full_python.models import MarketBar


NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
RTH_START = time(9, 30)
RTH_END = time(16, 0)


def is_rth_bar(timestamp_utc: str) -> bool:
    local_time = _parse_utc_timestamp(timestamp_utc).astimezone(NEW_YORK).time()
    return RTH_START <= local_time < RTH_END


def filter_bars_by_session(
    bars: Iterable[MarketBar],
    session: str,
) -> Iterator[MarketBar]:
    normalized = session.lower()
    if normalized == "all":
        yield from bars
        return
    if normalized != "rth":
        raise ValueError(f"Unsupported session: {session}")
    for bar in bars:
        if is_rth_bar(bar.timestamp_utc):
            yield bar


def _parse_utc_timestamp(timestamp_utc: str) -> datetime:
    if timestamp_utc.endswith("Z"):
        timestamp_utc = f"{timestamp_utc[:-1]}+00:00"
    parsed = datetime.fromisoformat(timestamp_utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
