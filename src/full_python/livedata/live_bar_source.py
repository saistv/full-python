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
from full_python.data.validation import market_bar_value_issues
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
        session_end_minutes_et: Optional[int] = None,
        on_wait: Optional[Callable[[], None]] = None,
        wait_slice_seconds: float = 2.0,
    ) -> None:
        self._feed = feed
        self._clock = clock
        self._authority = authority
        self._window = active_window
        self._position_open = position_provider
        self._grace = grace_seconds
        self._session_end_minutes_et = session_end_minutes_et
        self._last_emitted_ts: Optional[str] = None
        # Review 2026-07-19 P1-1: the single blocking feed wait starved the
        # order pump's heartbeat/event cadence for up to a full bar. Waits
        # are now sliced, invoking on_wait between slices so maintenance
        # (heartbeats, account events, reconciliation) runs at sub-bar
        # cadence instead of bar cadence.
        self._on_wait = on_wait
        self._wait_slice = max(0.1, float(wait_slice_seconds))

    def __iter__(self) -> Iterator[MarketBar]:
        return self

    def __next__(self) -> MarketBar:
        while True:
            if self._session_ended() and not self._position_open():
                raise StopIteration
            cold_start = self._last_emitted_ts is None
            expected = (
                self._current_minute() if cold_start
                else _parse(self._last_emitted_ts) + timedelta(minutes=1)
            )
            deadline = self._poll_deadline(expected)
            vbar = None
            while True:
                remaining = max(
                    0.0, (deadline - self._clock.now()).total_seconds()
                )
                slice_seconds = (
                    remaining if self._on_wait is None
                    else min(remaining, self._wait_slice)
                )
                vbar = self._feed.next_bar(slice_seconds)
                if vbar is not None:
                    break
                if remaining <= slice_seconds:
                    break  # the full deadline budget is spent
                self._on_wait()
            if vbar is None:
                if self._armed(expected):
                    expected_label = _to_iso_z(expected)
                    if cold_start:
                        expected_label = f"{expected_label} (cold-start)"
                    raise DataOutageError(
                        "no bar within grace for expected minute "
                        f"{expected_label} (armed)"
                    )
                if not cold_start:
                    self._last_emitted_ts = _to_iso_z(expected)  # advance past the gap
                continue
            if cold_start and self._clock.now() > deadline and self._armed(expected):
                raise DataOutageError(
                    "first bar arrived after cold-start deadline for expected minute "
                    f"{_to_iso_z(expected)} (armed)"
                )
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

    def _current_minute(self) -> datetime:
        return self._clock.now().astimezone(timezone.utc).replace(
            second=0, microsecond=0
        )

    def _deadline(self, expected: datetime) -> datetime:
        return expected + timedelta(minutes=1, seconds=self._grace)

    def _poll_deadline(self, expected: datetime) -> datetime:
        deadline = self._deadline(expected)
        session_end = self._session_end_deadline()
        if session_end is not None:
            deadline = min(deadline, session_end)
        return deadline

    def _session_end_deadline(self) -> Optional[datetime]:
        if self._session_end_minutes_et is None:
            return None
        session = classify_timestamp(_to_iso_z(self._clock.now()))
        hours, minutes = divmod(self._session_end_minutes_et, 60)
        return session.timestamp_et.replace(
            hour=hours, minute=minutes, second=0, microsecond=0
        ).astimezone(timezone.utc)

    def _session_ended(self) -> bool:
        if self._session_end_minutes_et is None:
            return False
        session = classify_timestamp(_to_iso_z(self._clock.now()))
        return session.minutes_from_midnight_et >= self._session_end_minutes_et

    def _armed(self, moment: Optional[datetime]) -> bool:
        if self._position_open():
            return True
        if moment is None:
            return False
        session = classify_timestamp(_to_iso_z(moment))
        return session.is_rth and self._window.contains(session.minutes_from_midnight_et)

    def _normalize(self, vbar: VendorBar) -> MarketBar:
        session = classify_timestamp(vbar.timestamp_utc)
        front = self._authority.front_contract(session.session_date)
        if vbar.symbol != front:
            raise DataIntegrityError(
                f"vendor symbol {vbar.symbol!r} is not the front contract "
                f"{front!r} for session {session.session_date}"
            )
        bar = MarketBar(
            timestamp_utc=vbar.timestamp_utc, symbol=front,
            open=vbar.open, high=vbar.high, low=vbar.low,
            close=vbar.close, volume=vbar.volume,
        )
        issues = market_bar_value_issues(bar)
        if issues:
            kind, detail = issues[0]
            raise DataIntegrityError(f"{kind}: {detail}")
        return bar

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
