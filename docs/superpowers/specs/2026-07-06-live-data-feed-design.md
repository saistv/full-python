# Live Data Feed (Live-Engine Sub-Project 2) — Design

## Context

Second of the live-engine sub-projects (1: execution core — DONE and
merged; 2: this; 3: Tradovate adapter; 4: Gate 5-7 tooling). The
execution core's `LiveLoop` consumes a `BarSource` protocol and today
runs against `RecordedBarSource` (a finite list). This sub-project
builds the *live* `BarSource`: it turns a stream of finalized 1-minute
bars into the exact `MarketBar` objects the backtester uses, enforces
which futures contract is authoritative, and halts-and-flattens on a
data outage.

Everything here is vendor-agnostic and offline-testable. The actual
Tradovate market-data subscription / wire / auth is sub-project 3; this
sub-project defines and tests everything up to the `MarketDataFeed`
seam that sub-project 3 must satisfy.

**Approved decisions (brainstorming, 2026-07-06):**
- Feed input = **finalized 1-minute bars** (not ticks to aggregate) —
  the granularity the backtest was built from, maximizing parity.
- Outage-while-in-position action = **halt + flatten** — never hold an
  unmanaged position through a blind feed.
- Architecture = Approach A: `LiveBarSource` wrapping a
  `MarketDataFeed` protocol with an injected `Clock`; single-threaded,
  deterministic, testable with a scripted feed + fake clock.
- **The trading window must stay malleable** — the operator will retune
  it through research and must not be locked to 9:30-10:00. This is
  already true at the strategy level: `AdaptiveTrendConfig.
  entry_start_minutes_et` / `entry_end_minutes_et` (defaults 570 / 600)
  gate entries, and the backstop is `SimulationConfig.flatten_hour_et` /
  `flatten_minute_et`. **Requirement for this sub-project: the outage
  arming window is INJECTED from those same config values — never a
  hardcoded constant.** Changing the trading window in config
  automatically moves the outage-arming window; there is one source of
  truth for "when is the strategy active."

## Established seams (reused, not rebuilt)

- `models.MarketBar`: `timestamp_utc: str` (ISO-8601 UTC, `Z` suffix,
  **minute-open**, e.g. `2021-03-16T00:00:00Z`), `symbol`, OHLCV.
- `data.sessions.classify_timestamp` → `SessionInfo` (CME session_date
  rolling at 18:00 ET, `is_rth` [09:30,16:00) ET, `is_rth_open_window`).
- `data.databento.front_contract_for_session(session_date, root,
  roll_overrides)` → specific front contract code (`NQZ5`), and
  `roll_date` — the validated roll authority (expiry−3 cal days with an
  observed-override table).
- `execution.live_loop.BarSource` protocol (`__iter__ -> Iterator[
  MarketBar]`) and `LiveLoop`, which already flattens-and-halts on
  `ExecutionInvariantError`.

## Architecture

New package `src/full_python/livedata/`. Depends on `data`, `models`,
`execution` (protocol types); nothing under `data`/`simulation`/`risk`
depends back on it (layering: livedata → {data, models, execution}).

### 1. `livedata/feed.py` — the vendor seam

```python
@dataclass(frozen=True)
class VendorBar:
    symbol: str          # specific contract, e.g. "NQZ5"
    timestamp_utc: str    # minute-open, ISO-8601 UTC "…Z"
    open: float
    high: float
    low: float
    close: float
    volume: float

class MarketDataFeed(Protocol):
    def next_bar(self, timeout_seconds: float) -> Optional[VendorBar]: ...
        # blocks up to timeout_seconds; returns the next finalized bar,
        # or None if none arrived in the window. Sub-project 3's Tradovate
        # adapter implements this; nothing else about the vendor leaks in.
```

### 2. `livedata/clock.py` — injectable time

```python
class Clock(Protocol):
    def now(self) -> datetime: ...   # timezone-aware UTC

class SystemClock:                    # production
    def now(self): return datetime.now(timezone.utc)
```

Tests inject a `FakeClock` (settable `now`) so outage timing runs in
microseconds and deterministically. This is what makes the
capital-safety behavior testable without waiting real seconds.

### 3. `livedata/contract_authority.py` — which contract is tradeable

```python
class ContractAuthority:
    def __init__(self, root="NQ", roll_overrides=None): ...
    def front_contract(self, session_date: date) -> str: ...
        # delegates to data.databento.front_contract_for_session
```

Thin, single-purpose. Rolls occur only at session boundaries; the
strategy is always flat overnight (backstop flatten 15:59), so it never
holds a position across a roll — roll handling therefore never has to
reconcile an open position. The `LiveBarSource` stamps
`MarketBar.symbol` with the front contract so downstream `OrderIntent`s
route to the tradeable instrument. OHLCV is untouched, so **live
signals stay byte-identical to backtest**; only the routing symbol
differs (`NQ<code>` live vs. `NQ1!` in the recorded CSV), which the
backtest never exercised.

### 4. `livedata/live_bar_source.py` — `LiveBarSource`

Implements `BarSource`. Constructed with a `MarketDataFeed`, a `Clock`,
a `ContractAuthority`, a `position_provider` (a zero-arg callable
returning whether a position is currently open — wired to
`broker.position is not None`), an `ActiveWindow`, and a `grace_seconds`
parameter (default 25.0).

`ActiveWindow` is a tiny frozen value type carrying
`start_minutes_et` and `end_minutes_et` (minutes-from-midnight ET),
constructed from config by the caller — e.g. `ActiveWindow(
start_minutes_et=config.entry_start_minutes_et, end_minutes_et=
sim_config.flatten_minutes_et)`. The `LiveBarSource` treats
`start <= session.minutes_from_midnight_et < end` as "strategy active."
It holds NO literal window constants; retuning the trading window in
config moves the arming window with zero code change. Its
`__iter__`/`__next__`:

1. Compute the next expected minute-open timestamp (monotonic from the
   last emitted bar, or the current minute on cold start).
2. Poll `feed.next_bar(timeout)` where `timeout` is the remaining time
   until `expected_minute + 60s + grace_seconds` per the clock.
3. **Normalize + validate** an arriving bar:
   - Re-stamp `symbol` to `ContractAuthority.front_contract(session)`;
     if the vendor symbol is neither the front nor a known adjacent
     contract at a roll boundary → `DataIntegrityError`.
   - Timestamp must be strictly greater than the last emitted bar's
     (monotonic). Equal/backwards → `DataIntegrityError` (duplicate /
     out-of-order).
   - Yield the `MarketBar`.
4. **Outage detection (session-armed):** the detector is *armed* when
   `position_provider()` is true OR the expected minute falls inside the
   injected `ActiveWindow`. When armed and either (a) the feed times out
   past the grace window, or (b) an arriving bar's timestamp skips an
   expected interior RTH minute → `DataOutageError`. When *disarmed*
   (flat and outside the `ActiveWindow` — CME maintenance break,
   overnight, weekend), a gap is normal: advance the expected minute and
   keep waiting, no raise. Because the window is injected, if the
   operator later moves entries to (say) 10:00-11:00, the arming window
   follows automatically with no change here.

Errors (`livedata/errors.py`): `DataOutageError`, `DataIntegrityError`,
both subclassing a common `LiveDataError(RuntimeError)`.

### 5. `LiveLoop` — one additive change (the only touch to merged code)

`LiveLoop.run` gains a sibling `except LiveDataError` beside the
existing `except ExecutionInvariantError`, performing the identical
action: flatten via the broker at the last-seen bar, append a
`STATE_TRANSITION` with `{"transition": "execution_halt", "reason":
"data_outage", "error": str(exc)}`, set `halted_reason`, stop. A clean
feed never raises, so the existing sim-identity property is unaffected
(no `LiveDataError` path is reachable from `RecordedBarSource`).

## Data flow

`for bar in LiveBarSource: …` — each iteration blocks on the feed up to
the grace window, then yields a normalized `MarketBar` or raises a halt
condition. `LiveLoop` processes that bar exactly as it processes a
recorded bar. Live and backtest run the *same loop over the same
`MarketBar` type*; only the source differs. That is the parity
guarantee, inherited from sub-project 1.

## Testing (all offline, no network, fake clock)

1. **Normalization parity:** a `VendorBar` maps to the exact
   `MarketBar` the CSV loader produces for the same values (fields,
   timestamp string, symbol-restamping rule).
2. **Contract authority:** `front_contract` returns the right code
   across the observed roll boundaries already documented for the
   Databento continuous (e.g. the NQZ5→NQH6→NQM6→NQU6 handovers); a
   `roll_overrides` entry pins a divergent date.
3. **Outage — armed:** fake clock advanced past `expected + 60 +
   grace`, feed returns `None`, position open → `DataOutageError`.
4. **Outage — interior gap while armed:** feed returns a bar that skips
   an RTH minute → `DataOutageError`.
5. **No false outage — disarmed:** flat + outside the active window,
   feed returns `None` for a maintenance-break minute → no raise,
   expected minute advances.
5b. **Window is malleable, not hardcoded:** construct two
   `LiveBarSource`s with *different* `ActiveWindow`s (e.g. 9:30-10:00
   vs 10:00-11:00); an identical flat + feed-timeout at 10:30 raises
   `DataOutageError` for the second (armed) and not the first
   (disarmed). This pins the injected-window requirement — a future
   edit that re-hardcodes 9:30 fails here.
6. **Integrity:** backwards timestamp and duplicate timestamp each →
   `DataIntegrityError`.
7. **Integration:** `LiveLoop` + `LiveBarSource(scripted feed)` +
   `PaperBroker`; a scripted mid-position outage yields a flatten + a
   `data_outage` halt event, `halted_reason` set, position closed.
8. **Regression:** the existing sim-identity tests still pass unchanged
   (the new `except` clause is unreachable from `RecordedBarSource`).

## Out of scope (sub-project 3)

- The real Tradovate market-data subscription, wire protocol, auth,
  reconnect/backfill — everything behind the `MarketDataFeed` seam.
- Tick aggregation (deliberately excluded: finalized-bar input chosen).
- Backfilling missed bars after a recovered outage (the policy is halt
  for the session, not resume — resume/backfill is a later decision).
