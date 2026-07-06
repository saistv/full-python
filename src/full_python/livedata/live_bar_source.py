"""The live BarSource: finalized vendor bars -> the exact MarketBar the
backtester uses, with contract authority, integrity checks, and
session-armed outage detection. Single-threaded and clock-injected, so
the outage/halt behavior is deterministic and testable offline.

The outage-arming window is INJECTED (ActiveWindow), never a literal:
retuning the trading window in config moves arming with no change here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator, Optional

from full_python.data.sessions import classify_timestamp
from full_python.livedata.clock import Clock
from full_python.livedata.contract_authority import ContractAuthority
from full_python.livedata.errors import DataIntegrityError, DataOutageError
from full_python.livedata.feed import MarketDataFeed, VendorBar
from full_python.models import MarketBar


@dataclass(frozen=True)
class ActiveWindow:
    start_minutes_et: int
    end_minutes_et: int

    def contains(self, minutes_from_midnight_et: int) -> bool:
        return self.start_minutes_et <= minutes_from_midnight_et < self.end_minutes_et


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _to_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LiveBarSource:
    def __init__(
        self,
        feed: MarketDataFeed,
        clock: Clock,
        authority: ContractAuthority,
        active_window: ActiveWindow,
        position_provider: Callable[[], bool],
        grace_seconds: float = 25.0,
    ) -> None:
        self._feed = feed
        self._clock = clock
        self._authority = authority
        self._window = active_window
        self._position_open = position_provider
        self._grace = grace_seconds
        self._last_emitted_ts: Optional[str] = None

    def __iter__(self) -> Iterator[MarketBar]:
        return self

    def __next__(self) -> MarketBar:
        while True:
            expected = (
                None if self._last_emitted_ts is None
                else _parse(self._last_emitted_ts) + timedelta(minutes=1)
            )
            vbar = self._feed.next_bar(self._timeout_seconds(expected))
            if vbar is None:
                if self._armed(expected):
                    raise DataOutageError(
                        "no bar within grace for expected minute "
                        f"{_to_iso_z(expected) if expected else '<cold-start>'} (armed)"
                    )
                if expected is not None:
                    self._last_emitted_ts = _to_iso_z(expected)  # advance past the gap
                continue
            bar = self._normalize(vbar)
            self._validate_monotonic(bar)
            # Arm the gap check off `expected` (the first missing minute /
            # start of the gap), NOT the arriving bar's timestamp. A gap that
            # begins inside the active window but whose next bar lands just
            # after the window closes must still be caught -- arming off the
            # (later) arrival moment would silently swallow exactly that case.
            if self._armed(expected):
                self._validate_no_interior_gap(bar)
            self._last_emitted_ts = bar.timestamp_utc
            return bar

    def _timeout_seconds(self, expected: Optional[datetime]) -> float:
        if expected is None:
            return 60.0  # cold start: wait ~a minute for the first bar
        deadline = expected + timedelta(minutes=1, seconds=self._grace)
        return max(0.0, (deadline - self._clock.now()).total_seconds())

    def _armed(self, moment: Optional[datetime]) -> bool:
        if self._position_open():
            return True
        if moment is None:
            return False
        session = classify_timestamp(_to_iso_z(moment))
        return self._window.contains(session.minutes_from_midnight_et)

    def _normalize(self, vbar: VendorBar) -> MarketBar:
        session = classify_timestamp(vbar.timestamp_utc)
        front = self._authority.front_contract(session.session_date)
        if vbar.symbol != front:
            raise DataIntegrityError(
                f"vendor symbol {vbar.symbol!r} is not the front contract "
                f"{front!r} for session {session.session_date}"
            )
        return MarketBar(
            timestamp_utc=vbar.timestamp_utc, symbol=front,
            open=vbar.open, high=vbar.high, low=vbar.low,
            close=vbar.close, volume=vbar.volume,
        )

    def _validate_monotonic(self, bar: MarketBar) -> None:
        # Fixed-width ISO-8601 UTC strings sort chronologically.
        if self._last_emitted_ts is not None and bar.timestamp_utc <= self._last_emitted_ts:
            raise DataIntegrityError(
                f"non-monotonic bar {bar.timestamp_utc} <= last {self._last_emitted_ts}"
            )

    def _validate_no_interior_gap(self, bar: MarketBar) -> None:
        if self._last_emitted_ts is None:
            return
        expected = _parse(self._last_emitted_ts) + timedelta(minutes=1)
        if _parse(bar.timestamp_utc) != expected:
            raise DataOutageError(
                f"armed interior gap: got {bar.timestamp_utc}, "
                f"expected {_to_iso_z(expected)}"
            )
