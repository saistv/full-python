# Live Data Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A vendor-agnostic `LiveBarSource` that turns a stream of finalized 1-minute bars into the exact `MarketBar` objects the backtester uses, enforces contract authority, and halts-and-flattens on a data outage — feeding the already-built `LiveLoop`.

**Architecture:** New `src/full_python/livedata/` package: a `MarketDataFeed` protocol (the seam sub-project 3's Tradovate adapter satisfies), an injected `Clock`, a `ContractAuthority` wrapping the validated roll logic, and a `LiveBarSource` implementing the LiveLoop's `BarSource`. The outage-arming window is injected from config (never hardcoded). One additive `LiveLoop` change adds a data-outage halt that flattens.

**Tech Stack:** Python 3 stdlib. Consumes: `models.MarketBar`, `data.sessions.classify_timestamp` → `SessionInfo` (fields `session_date: date`, `minutes_from_midnight_et: int`, `is_rth: bool`), `data.databento.front_contract_for_session(session_date, root="NQ", roll_overrides=None) -> str`, `execution.live_loop.LiveLoop`, `execution.paper_broker.PaperBroker` (tests).

## Global Constraints

- **The trading window is malleable, never hardcoded.** The outage-arming window is an injected `ActiveWindow(start_minutes_et, end_minutes_et)` built by the caller from `AdaptiveTrendConfig.entry_start_minutes_et` and the flatten time. No literal minute constant (570, 600, 9*60+30, …) may appear in `livedata/`.
- Layering: `livedata/` may import from `data`, `models`, `execution`; nothing under `data`/`simulation`/`risk` may import from `livedata`.
- Bar parity: a normalized `MarketBar` carries the vendor bar's `timestamp_utc` verbatim (minute-open, ISO-8601 UTC `…Z`) and OHLCV unchanged; only `symbol` is set to the session's front contract.
- Data-outage halt **flattens** (broker is authoritative on a data loss); the existing invariant halt deliberately does not — do not merge the two paths.
- `python3 -m pytest -q` stays green. Worktree baseline before Task 1: **174 passed, 3 skipped**. Existing sim-identity tests must remain unchanged (the new `except` is unreachable from `RecordedBarSource`).
- Commit style `feat: ...`.

---

### Task 1: livedata scaffolding — feed protocol, clock, errors

**Files:**
- Create: `src/full_python/livedata/__init__.py`
- Create: `src/full_python/livedata/feed.py`
- Create: `src/full_python/livedata/clock.py`
- Create: `src/full_python/livedata/errors.py`
- Test: `tests/test_livedata_scaffolding.py`

**Interfaces:**
- Produces (later tasks rely on these exact names):
  - `feed.VendorBar(symbol: str, timestamp_utc: str, open: float, high: float, low: float, close: float, volume: float)` — frozen dataclass
  - `feed.MarketDataFeed` Protocol: `next_bar(self, timeout_seconds: float) -> Optional[VendorBar]`
  - `clock.Clock` Protocol: `now(self) -> datetime` (tz-aware UTC); `clock.SystemClock` implementing it
  - `errors.LiveDataError(RuntimeError)`, `errors.DataOutageError(LiveDataError)`, `errors.DataIntegrityError(LiveDataError)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_livedata_scaffolding.py`:

```python
from datetime import datetime, timezone

import pytest

from full_python.livedata.clock import Clock, SystemClock
from full_python.livedata.errors import (
    DataIntegrityError,
    DataOutageError,
    LiveDataError,
)
from full_python.livedata.feed import MarketDataFeed, VendorBar


def test_vendor_bar_is_frozen_with_expected_fields():
    vb = VendorBar(symbol="NQZ5", timestamp_utc="2025-11-03T14:31:00Z",
                   open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)
    assert vb.symbol == "NQZ5"
    assert vb.timestamp_utc == "2025-11-03T14:31:00Z"
    assert (vb.open, vb.high, vb.low, vb.close, vb.volume) == (1.0, 2.0, 0.5, 1.5, 10.0)
    with pytest.raises(Exception):
        vb.close = 9.0  # frozen


def test_system_clock_now_is_timezone_aware_utc():
    now = SystemClock().now()
    assert isinstance(now, datetime)
    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(None)


def test_error_hierarchy():
    assert issubclass(DataOutageError, LiveDataError)
    assert issubclass(DataIntegrityError, LiveDataError)
    assert issubclass(LiveDataError, RuntimeError)
    assert not issubclass(DataIntegrityError, DataOutageError)


def test_protocols_are_structural():
    # A minimal object satisfying each protocol type-checks structurally.
    class _Feed:
        def next_bar(self, timeout_seconds: float):
            return None
    class _Clk:
        def now(self):
            return datetime.now(timezone.utc)
    assert isinstance(_Feed(), MarketDataFeed)
    assert isinstance(_Clk(), Clock)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_livedata_scaffolding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'full_python.livedata'`

- [ ] **Step 3: Write the implementation**

Create `src/full_python/livedata/__init__.py`:

```python
"""Live market-data feed (Gate 5+). Vendor-agnostic; the Tradovate wire is sub-project 3."""
```

Create `src/full_python/livedata/errors.py`:

```python
"""Live-data failure signals. A LiveDataError propagating out of the
BarSource halts (and flattens) the LiveLoop -- the broker is still
authoritative on a data loss, so flattening is safe and desired.
"""
from __future__ import annotations


class LiveDataError(RuntimeError):
    pass


class DataOutageError(LiveDataError):
    """No bar arrived when one was expected (feed stalled or gapped)."""


class DataIntegrityError(LiveDataError):
    """A bar that cannot be trusted: wrong contract, or non-monotonic time."""
```

Create `src/full_python/livedata/feed.py`:

```python
"""The vendor seam: finalized 1-minute bars in, nothing else about the
vendor leaks past this module. Sub-project 3's Tradovate adapter
implements MarketDataFeed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class VendorBar:
    symbol: str          # specific contract, e.g. "NQZ5"
    timestamp_utc: str    # minute-open, ISO-8601 UTC "...Z"
    open: float
    high: float
    low: float
    close: float
    volume: float


@runtime_checkable
class MarketDataFeed(Protocol):
    def next_bar(self, timeout_seconds: float) -> Optional[VendorBar]:
        """Block up to timeout_seconds; return the next finalized bar, or
        None if none arrived in the window."""
        ...
```

Create `src/full_python/livedata/clock.py`:

```python
"""Injectable time so outage timing is deterministic in tests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_livedata_scaffolding.py -v`
Expected: 4 passed

- [ ] **Step 5: Full suite + commit**

Run: `python3 -m pytest -q` → expected 178 passed, 3 skipped

```bash
git add src/full_python/livedata/ tests/test_livedata_scaffolding.py
git commit -m "feat: livedata scaffolding -- feed protocol, clock, error hierarchy"
```

---

### Task 2: ContractAuthority

**Files:**
- Create: `src/full_python/livedata/contract_authority.py`
- Test: `tests/test_contract_authority.py`

**Interfaces:**
- Consumes: `data.databento.front_contract_for_session(session_date, root, roll_overrides)`.
- Produces: `ContractAuthority(root: str = "NQ", roll_overrides: Optional[dict[str, date]] = None)` with `front_contract(self, session_date: date) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_contract_authority.py`:

```python
from datetime import date

from full_python.data.databento import front_contract_for_session
from full_python.livedata.contract_authority import ContractAuthority


def test_front_contract_delegates_to_validated_roll_logic():
    auth = ContractAuthority(root="NQ")
    # Equivalence to the already-validated function across a spread of
    # sessions spanning multiple quarterly contracts -- ContractAuthority
    # must not reimplement roll math, only wrap it.
    for d in (date(2025, 1, 15), date(2025, 3, 20), date(2025, 6, 2),
              date(2025, 9, 10), date(2025, 12, 1), date(2026, 2, 26),
              date(2026, 6, 20)):
        assert auth.front_contract(d) == front_contract_for_session(d, "NQ", None)


def test_roll_override_is_passed_through():
    override = {"NQH6": date(2026, 3, 10)}
    auth = ContractAuthority(root="NQ", roll_overrides=override)
    for d in (date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11)):
        assert auth.front_contract(d) == front_contract_for_session(d, "NQ", override)
    # the override actually changes at least one session's answer vs no-override
    assert any(
        auth.front_contract(d) != front_contract_for_session(d, "NQ", None)
        for d in (date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11))
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_contract_authority.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` (no `contract_authority`)

- [ ] **Step 3: Write the implementation**

Create `src/full_python/livedata/contract_authority.py`:

```python
"""Which specific futures contract is tradeable for a session.

Thin wrapper over the validated data.databento roll authority (expiry-3
calendar days with an observed-override table). Rolls occur only at
session boundaries; the strategy is always flat overnight (backstop
flatten), so this never has to reconcile an open position across a roll.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from full_python.data.databento import front_contract_for_session


class ContractAuthority:
    def __init__(
        self, root: str = "NQ", roll_overrides: Optional[dict[str, date]] = None
    ) -> None:
        self._root = root
        self._roll_overrides = roll_overrides

    def front_contract(self, session_date: date) -> str:
        return front_contract_for_session(session_date, self._root, self._roll_overrides)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_contract_authority.py -v`
Expected: 2 passed

- [ ] **Step 5: Full suite + commit**

Run: `python3 -m pytest -q` → expected 180 passed, 3 skipped

```bash
git add src/full_python/livedata/contract_authority.py tests/test_contract_authority.py
git commit -m "feat: ContractAuthority wrapping the validated roll logic"
```

---

### Task 3: LiveBarSource (ActiveWindow + normalization + integrity + outage)

**Files:**
- Create: `src/full_python/livedata/live_bar_source.py`
- Test: `tests/test_live_bar_source.py`

**Interfaces:**
- Consumes: `feed.VendorBar`/`MarketDataFeed`, `clock.Clock`, `contract_authority.ContractAuthority`, `errors.DataOutageError`/`DataIntegrityError`, `models.MarketBar`, `data.sessions.classify_timestamp`.
- Produces:
  - `ActiveWindow(start_minutes_et: int, end_minutes_et: int)` frozen dataclass with `contains(self, minutes_from_midnight_et: int) -> bool` (`start <= m < end`).
  - `LiveBarSource(feed, clock, authority, active_window, position_provider: Callable[[], bool], grace_seconds: float = 25.0)` implementing `__iter__ -> Iterator[MarketBar]` and `__next__ -> MarketBar`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_bar_source.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_live_bar_source.py -v`
Expected: FAIL — `ModuleNotFoundError` (no `live_bar_source`)

- [ ] **Step 3: Write the implementation**

Create `src/full_python/livedata/live_bar_source.py`:

```python
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
            if self._armed(_parse(bar.timestamp_utc)):
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
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_live_bar_source.py -v`
Expected: 8 passed

If a test fails, the tests are the source of truth for behavior — fix the implementation, not the test.

- [ ] **Step 5: Full suite + commit**

Run: `python3 -m pytest -q` → expected 188 passed, 3 skipped

```bash
git add src/full_python/livedata/live_bar_source.py tests/test_live_bar_source.py
git commit -m "feat: LiveBarSource -- normalization, integrity, session-armed outage"
```

---

### Task 4: LiveLoop data-outage halt (flatten) + integration

**Files:**
- Modify: `src/full_python/execution/live_loop.py` (add `last_bar` tracking + an `except LiveDataError` that flattens)
- Test: `tests/test_live_loop_data_outage.py`

**Interfaces:**
- Consumes: `LiveLoop`, `PaperBroker`, `RiskSupervisor`/`RiskSupervisorConfig`, `LiveBarSource`, `ScriptedFeed`/`FakeClock` patterns, `livedata.errors.DataOutageError`.
- Produces: no new public names; `LiveLoop.run` gains a data-outage halt path that flattens any open position and records `reason="data_outage"`.

**Semantic note (do not merge with the invariant path):** the existing `except ExecutionInvariantError` deliberately does NOT flatten — on an invariant violation the true position is unknown. A data outage is different: the broker is authoritative (we lost data, not the broker), so the outage path DOES flatten the open position at the last-seen bar before halting.

- [ ] **Step 1: Write the failing test**

Create `tests/test_live_loop_data_outage.py`:

```python
from datetime import date, datetime, timezone

from full_python.events import EventLedger, EventType
from full_python.execution.live_loop import LiveLoop
from full_python.execution.paper_broker import PaperBroker
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.livedata.contract_authority import ContractAuthority
from full_python.livedata.live_bar_source import ActiveWindow, LiveBarSource
from full_python.livedata.feed import VendorBar
from full_python.models import OrderIntent, StrategyResult
from full_python.simulation import SimulationConfig


class FakeClock:
    def __init__(self, now): self._now = now
    def now(self): return self._now


class ScriptedFeed:
    def __init__(self, items): self._items = list(items); self._i = 0
    def next_bar(self, timeout_seconds):
        if self._i >= len(self._items): return None
        item = self._items[self._i]; self._i += 1; return item


AUTH = ContractAuthority(root="NQ")
FRONT = AUTH.front_contract(date(2025, 11, 3))
CFG = SimulationConfig(point_value=2.0, commission_per_contract_round_trip=1.0,
                       entry_slippage_points=0.0, exit_slippage_points=0.0,
                       rth_open_extra_entry_slippage_points=0.0)


def _vbar(ts, c, o=None):
    o = c if o is None else o
    return VendorBar(symbol=FRONT, timestamp_utc=ts, open=o, high=c, low=c, close=c, volume=5.0)


class EnterThenSilent:
    """Buys on the first bar (fills next bar), then never signals."""
    def __init__(self): self._fired = False
    def on_bar(self, bar):
        if not self._fired:
            self._fired = True
            return StrategyResult(order_intents=(OrderIntent.market_entry(
                timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, side="buy",
                quantity=1, reason="scripted",
                metadata={"stop_price": bar.close - 50.0, "signal_price": bar.close}),))
        return StrategyResult()


def test_mid_position_outage_flattens_and_halts():
    # bars during RTH (armed window); a position opens, then the feed dries up.
    feed = ScriptedFeed([
        _vbar("2025-11-03T14:31:00Z", 100.0),  # signal -> entry pending
        _vbar("2025-11-03T14:32:00Z", 101.0),  # entry fills at open; position open
        None,                                   # feed stalls while in position -> outage
    ])
    ledger = EventLedger()
    strategy = EnterThenSilent()
    broker = PaperBroker(CFG, strategy, ledger)
    window = ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=16 * 60)
    src = LiveBarSource(feed, FakeClock(datetime(2025, 11, 3, 15, 0, tzinfo=timezone.utc)),
                        AUTH, window, position_provider=lambda: broker.position is not None)
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=CFG.point_value))

    result = LiveLoop(src, strategy, broker, sup, ledger).run()

    assert result.halted_reason is not None
    assert "data_outage" in result.halted_reason
    # the open position was flattened (a trade exists and the flatten closed it)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "data_outage"
    # a data_outage halt transition is in the ledger
    halts = [r for r in ledger.records
             if r.event_type == EventType.STATE_TRANSITION
             and r.payload.get("transition") == "execution_halt"
             and r.payload.get("reason") == "data_outage"]
    assert len(halts) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_live_loop_data_outage.py -v`
Expected: FAIL — `LiveLoop` does not yet catch `DataOutageError`, so it propagates out of `.run()` (or `halted_reason`/flatten assertions fail).

- [ ] **Step 3: Implement the LiveLoop change**

In `src/full_python/execution/live_loop.py`:

Add the import near the other execution imports:

```python
from full_python.livedata.errors import LiveDataError
```

Add `last_bar` tracking. Change the two lines at the top of `run`:

```python
        halted_reason: Optional[str] = None
        breach_flattened: set[str] = set()  # session_dates already acted on
        last_timestamp = ""  # for stamping a halt raised outside a live bar
        last_bar = None  # last MarketBar seen, for flattening on a data outage
```

Inside the loop, set it alongside `last_timestamp`:

```python
            for bar in self._bar_source:
                last_timestamp = bar.timestamp_utc
                last_bar = bar
```

Add the sibling `except` immediately after the existing `except ExecutionInvariantError` block (keep that block unchanged):

```python
        except LiveDataError as exc:
            halted_reason = f"data_outage: {exc}"
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=last_timestamp,
                payload={
                    "transition": "execution_halt",
                    "reason": "data_outage",
                    "error": str(exc),
                },
            )
            # Unlike an invariant halt (position unknown -> do not flatten),
            # a data outage leaves the BROKER authoritative -- flatten the
            # open position at the last-seen bar before halting.
            if last_bar is not None and self._broker.position is not None:
                self._broker.flatten(last_bar, "data_outage")
```

(Import `LiveLoop`'s module now depends on `livedata.errors`; this is the execution→livedata... NO — livedata imports execution. To avoid an import cycle, import `LiveDataError` INSIDE the `except`-guarding scope is not possible for an `except` clause. Instead: `livedata.errors` imports NOTHING from `execution`, so `execution.live_loop` importing `livedata.errors` is acyclic — `errors.py` has no execution imports. Verify `livedata/errors.py` imports only stdlib, which it does.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_live_loop_data_outage.py -v`
Expected: 1 passed

- [ ] **Step 5: Regression — sim-identity unchanged**

Run: `python3 -m pytest tests/test_live_loop_identity.py -q`
Expected: 3 passed, 1 skipped — the new `except` is unreachable from `RecordedBarSource`, so identity is untouched.

- [ ] **Step 6: Full suite + commit**

Run: `python3 -m pytest -q` → expected 189 passed, 3 skipped

```bash
git add src/full_python/execution/live_loop.py tests/test_live_loop_data_outage.py
git commit -m "feat: LiveLoop flattens and halts on a data outage"
```

---

## Post-merge / integration notes (controller, not a task)

- No live broker adapter still exists — this remains offline/paper only. Nothing here can place a real order.
- The `MarketDataFeed` protocol is the seam sub-project 3's Tradovate adapter implements; `ContractAuthority` tells that adapter which contract to subscribe to.

## Not in this plan (sub-project 3)

- The real Tradovate market-data subscription/wire/auth/reconnect behind `MarketDataFeed`.
- Backfill/resume after a recovered outage (policy is halt-for-session).
- Tick aggregation (finalized-bar input chosen).
