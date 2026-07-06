# MR Variant 2 — Opening Range Fade Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `OpeningRangeFadeStrategy` + `OpeningRangeFadeConfig` (MR variant 2, run 1) — fade failed opening-range breakouts with an ATR bracket — and register it as a runnable strategy.

**Architecture:** A decision-only strategy class following `VwapReversionStrategy` exactly (same interfaces, `Atr`/`DailyAdx` reuse, engine-owned fills + frozen bracket + 15:59 backstop). The ONLY new logic is the signal: build the 9:30–10:00 opening range, arm a breakout when price extends ≥ 1 ATR beyond it, and fade (with a 1-ATR stop / 2-ATR static target) when a bar closes back inside within the failure window.

**Tech Stack:** Python 3 stdlib + existing `full_python` modules: `indicators.Atr`, `regime.DailyAdx`, `data.sessions.classify_timestamp`, `models` (`MarketBar`/`OrderIntent`/`ExitDecision`/`SignalDecision`/`StrategyResult`/`Fill`/`Trade`), `simulation` (tests). Hypothesis/design: `docs/research/2026-07-06-mr-orfade-run1-hypothesis.md`.

## Global Constraints

- **Literature-faithful bracket, verbatim from the hypothesis:** static target anchored at entry, R:R exactly 2.0 (target = 2 × stop distance), stop = 1.0 × ATR(14) frozen, time stop 20 bars, daily ADX(14) < 20 gate, entry window 10:00–15:30 ET (`entry_start_minutes_et=600`, `entry_end_minutes_et=930`) — disjoint from AT's window by construction. Fill/execution semantics identical to the reconciled runs (engine-owned).
- **Extension threshold (principle 6):** a breakout only qualifies to be faded if the excursion beyond the OR edge reached ≥ `breakout_atr_mult × ATR(14)` (default 1.0) during the breakout.
- Decision-only: the strategy emits intents/exits; the engine owns fills, the stop/target bracket, and the backstop. Fade intents/fills carry `reason="opening_range_fade"`.
- No changes to `strategy/adaptive_trend*`, `strategy/vwap_reversion*`, `simulation/`, `execution/`, `livedata/`, `risk/`, `regime.py`, `models.py`.
- `python3 -m pytest -q` stays green. Worktree baseline before Task 1: **191 passed, 3 skipped**. Commit style `feat: ...`.

---

### Task 1: OpeningRangeFadeConfig

**Files:**
- Create: `src/full_python/strategy/opening_range_fade_config.py`
- Test: `tests/test_opening_range_fade.py`

**Interfaces:**
- Produces: `OpeningRangeFadeConfig` frozen dataclass with `to_dict()` and `parameter_hash()`, fields exactly as the hypothesis config block.

- [ ] **Step 1: Write the failing test**

Create `tests/test_opening_range_fade.py`:

```python
from full_python.strategy.opening_range_fade_config import OpeningRangeFadeConfig


def test_config_is_literature_faithful_baseline() -> None:
    c = OpeningRangeFadeConfig()
    assert c.stop_atr_mult <= 1.0            # principle 3: tight stop
    assert c.rr_multiple >= 2.0              # principle 2: R:R >= 2:1
    assert 15 <= c.time_stop_bars <= 20      # principle 4: short hold
    assert c.adx_max <= 20.0                 # principle 5: strict regime gate
    assert c.breakout_atr_mult >= 1.0        # principle 6: extension, not a poke
    assert c.entry_start_minutes_et >= 10 * 60   # disjoint from AT's 9:30-10:00
    assert c.or_start_minutes_et == 9 * 60 + 30  # OR = 9:30-10:00
    assert c.or_end_minutes_et == 10 * 60
    assert len(c.parameter_hash()) == 64
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_opening_range_fade.py -v`
Expected: FAIL — `ModuleNotFoundError` (no `opening_range_fade_config`)

- [ ] **Step 3: Write the config**

Create `src/full_python/strategy/opening_range_fade_config.py`:

```python
"""MR variant 2 -- opening range fade v1 config.

Literature-faithful per the MR research contract: ATR bracket with a
static 2:1 target, 1-ATR frozen stop, 20-bar time stop, daily ADX(14)<20
gate, 10:00-15:30 ET entry window (disjoint from Adaptive Trend). The
signal fades a FAILED 9:30-10:00 opening-range breakout (extension >= 1
ATR beyond the edge, then a close back inside within the failure window).
Hypothesis: docs/research/2026-07-06-mr-orfade-run1-hypothesis.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class OpeningRangeFadeConfig:
    name: str = "opening_range_fade_v1"
    atr_length: int = 14
    or_start_minutes_et: int = 9 * 60 + 30   # 9:30
    or_end_minutes_et: int = 10 * 60          # 10:00 (OR frozen here)
    entry_start_minutes_et: int = 10 * 60     # 10:00, disjoint from AT
    entry_end_minutes_et: int = 15 * 60 + 30  # 15:30
    breakout_atr_mult: float = 1.0
    failure_window_bars: int = 10
    stop_atr_mult: float = 1.0
    rr_multiple: float = 2.0
    time_stop_bars: int = 20
    adx_length: int = 14
    adx_max: float = 20.0
    cooldown_bars: int = 5
    contracts: int = 1
    tick_size: float = 0.25
    warmup_bars: int = 100

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_opening_range_fade.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add src/full_python/strategy/opening_range_fade_config.py tests/test_opening_range_fade.py
git commit -m "feat: OpeningRangeFadeConfig -- MR variant 2 literature-faithful baseline"
```

---

### Task 2: OpeningRangeFadeStrategy

**Files:**
- Create: `src/full_python/strategy/opening_range_fade.py`
- Test: `tests/test_opening_range_fade.py` (append)

**Interfaces:**
- Consumes: `OpeningRangeFadeConfig` (Task 1); `Atr`, `DailyAdx`, `classify_timestamp`, models.
- Produces: `OpeningRangeFadeStrategy(config)` with `on_bar(bar) -> StrategyResult`, `on_fill(fill)`, `on_trade_closed(trade)` — same shape as `VwapReversionStrategy`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_opening_range_fade.py`:

```python
from datetime import datetime, timedelta, timezone

from full_python.events import EventType
from full_python.models import MarketBar
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.opening_range_fade import OpeningRangeFadeStrategy


# June 2026 is EDT (UTC-4), so 13:30 UTC == 9:30 ET: minute 0 = 9:30,
# minute 30 = 10:00, minute 40 = 10:10 ET. DST-clean by construction.
_BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _ts(day: int, minute: int) -> str:
    d = _BASE + timedelta(days=(day // 5) * 7 + day % 5)
    return (d.replace(hour=13, minute=30) + timedelta(minutes=minute)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _bar(ts, o, h, l, c, v=100.0):
    return MarketBar(ts, "NQ", o, h, l, c, v)


def _chop_day(day: int) -> list[MarketBar]:
    """Flat, alternating-direction session -> low daily ADX, tiny ATR."""
    bars = []
    p = 20000.0
    for m in range(390):
        c = p + (0.25 if m % 2 == 0 else -0.25)
        bars.append(_bar(_ts(day, m), p, max(p, c) + 0.25, min(p, c) - 0.25, c))
        p = c
    return bars


def _or_then_failed_up_breakout(day: int, extend_points: float) -> list[MarketBar]:
    """9:30-10:00 flat OR ~[19995, 20005]; at 10:10 a bar spikes `extend_points`
    above OR high then closes back inside -> a failed upside breakout.
    """
    bars = []
    p = 20000.0
    for m in range(390):
        if m < 30:                    # 9:30-10:00 OR: flat range around 20000
            c = 20005.0 if m % 2 == 0 else 19995.0
            h, l = 20005.0, 19995.0
        elif m == 40:                 # 10:10: spike high above OR, close inside
            c = 20003.0               # closes back inside (< or_high 20005)
            h = 20005.0 + extend_points
            l = 19999.0
        else:                         # flat elsewhere
            c = p + (0.25 if m % 2 == 0 else -0.25)
            h, l = max(p, c) + 0.25, min(p, c) - 0.25
        bars.append(_bar(_ts(day, m), p, h, l, c))
        p = c
    return bars


def _warmup(days: int = 40) -> list[MarketBar]:
    out = []
    for d in range(days):
        out.extend(_chop_day(d))
    return out


def _run(bars, config=None):
    strategy = OpeningRangeFadeStrategy(config or OpeningRangeFadeConfig())
    sim = SimulationConfig(
        point_value=20.0, commission_per_contract_round_trip=10.0,
        entry_slippage_points=0.0, exit_slippage_points=0.0,
        rth_open_extra_entry_slippage_points=0.0,
    )
    return SimulationEngine(sim).run(bars, strategy)


def _fade_fills(result):
    return [r for r in result.ledger.records
            if r.event_type == EventType.FILL
            and r.payload.get("reason") == "opening_range_fade"]


def test_failed_upside_breakout_fires_a_short_fade() -> None:
    # 40 low-ADX warmup days, then a day with a big (10pt >> 1 ATR) failed
    # upside breakout -> expect a short fade entry.
    bars = _warmup(40) + _or_then_failed_up_breakout(40, extend_points=10.0)
    result = _run(bars)
    fills = _fade_fills(result)
    assert len(fills) >= 1
    assert fills[0].payload["side"] == "sell"  # fade the failed UP breakout -> short


def test_subthreshold_poke_does_not_fade() -> None:
    # Same setup but the breakout barely pokes above the OR (0.5pt < 1 ATR):
    # not an EXTENSION, so no fade.
    bars = _warmup(40) + _or_then_failed_up_breakout(40, extend_points=0.5)
    result = _run(bars)
    assert len(_fade_fills(result)) == 0


def test_symmetric_failed_downside_breakout_fires_a_long_fade() -> None:
    def failed_down(day):
        bars = []
        p = 20000.0
        for m in range(390):
            if m < 30:
                c = 20005.0 if m % 2 == 0 else 19995.0
                h, l = 20005.0, 19995.0
            elif m == 40:                 # spike LOW below OR, close inside
                c = 19997.0               # back inside (> or_low 19995)
                h, l = 20001.0, 19995.0 - 10.0
            else:
                c = p + (0.25 if m % 2 == 0 else -0.25)
                h, l = max(p, c) + 0.25, min(p, c) - 0.25
            bars.append(_bar(_ts(day, m), p, h, l, c))
            p = c
        return bars
    bars = _warmup(40) + failed_down(40)
    fills = _fade_fills(_run(bars))
    assert len(fills) >= 1
    assert fills[0].payload["side"] == "buy"  # fade the failed DOWN breakout -> long


def test_trending_day_adx_gate_blocks_the_fade() -> None:
    # Warmup is strongly TRENDING (one direction every day) -> daily ADX high
    # -> even a clean failed breakout is gated out.
    def trend_day(day):
        bars = []
        p = 20000.0 + day * 50.0  # each day gaps up 50 -> persistent uptrend
        for m in range(390):
            c = p + 0.5  # drift up all day
            bars.append(_bar(_ts(day, m), p, c + 0.25, p - 0.25, c))
            p = c
        return bars
    bars = [b for d in range(40) for b in trend_day(d)]
    bars += _or_then_failed_up_breakout(40, extend_points=10.0)
    # not asserting zero unconditionally (ADX may warm slowly); assert the
    # gate is *consulted*: a trending-day rejection appears OR no fade fires.
    result = _run(bars)
    rejects = [r for r in result.ledger.records
               if r.event_type == EventType.REJECTION
               and r.payload.get("reason") == "adx_trending"]
    assert len(_fade_fills(result)) == 0 or len(rejects) >= 1
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_opening_range_fade.py -v`
Expected: config test passes; the 4 new tests FAIL — `ModuleNotFoundError` (no `opening_range_fade`)

- [ ] **Step 3: Write the strategy**

Create `src/full_python/strategy/opening_range_fade.py`:

```python
"""MR variant 2 -- opening range fade v1.

Fades a FAILED breakout of the 9:30-10:00 ET opening range on
non-trending days. A breakout arms when price extends >= breakout_atr_mult
x ATR(14) beyond the OR edge; it FAILS (and we fade) when a bar closes
back inside the range within failure_window bars. Bracket: 1-ATR frozen
stop, static 2:1 target, 20-bar time stop, ADX(14)<20 gate, 10:00-15:30
entry window (disjoint from Adaptive Trend). Decision-only, like
VwapReversionStrategy: the engine owns fills and the bracket.
"""
from __future__ import annotations

from typing import Optional

from full_python.data.sessions import classify_timestamp
from full_python.indicators import Atr
from full_python.models import (
    ExitDecision,
    Fill,
    MarketBar,
    OrderIntent,
    SignalDecision,
    StrategyResult,
    Trade,
)
from full_python.regime import DailyAdx
from full_python.strategy.opening_range_fade_config import OpeningRangeFadeConfig


class OpeningRangeFadeStrategy:
    def __init__(self, config: OpeningRangeFadeConfig) -> None:
        self.config = config
        self._atr = Atr(config.atr_length)
        self._adx = DailyAdx(config.adx_length)
        self._adx_value: Optional[float] = None
        self._bar_index = -1
        self._session_date: Optional[str] = None

        self._session_high = float("-inf")
        self._session_low = float("inf")
        self._session_close = 0.0

        # Opening range for the current session (built over [or_start, or_end)).
        self._or_high: Optional[float] = None
        self._or_low: Optional[float] = None

        # Breakout state, per side.
        self._up_active = False
        self._up_extended = False
        self._up_age = 0
        self._dn_active = False
        self._dn_extended = False
        self._dn_age = 0

        self._position_side: Optional[str] = None
        self._bars_in_trade = 0
        self._bars_since_exit = 999
        self._entry_pending = False
        self._entry_pending_age = 0

    def on_fill(self, fill: Fill) -> None:
        if fill.reason == "opening_range_fade":
            self._position_side = "long" if fill.side == "buy" else "short"
            self._bars_in_trade = 0
            self._entry_pending = False
            self._entry_pending_age = 0

    def on_trade_closed(self, trade: Trade) -> None:
        self._position_side = None
        self._bars_since_exit = 0
        self._entry_pending = False

    def _quantize(self, price: float) -> float:
        tick = self.config.tick_size
        return round(price / tick) * tick

    def _reset_session(self) -> None:
        self._session_high = float("-inf")
        self._session_low = float("inf")
        self._or_high = None
        self._or_low = None
        self._up_active = self._up_extended = False
        self._dn_active = self._dn_extended = False
        self._up_age = self._dn_age = 0
        self._entry_pending = False
        self._entry_pending_age = 0

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        config = self.config
        self._bar_index += 1
        session = classify_timestamp(bar.timestamp_utc)
        session_iso = session.session_date.isoformat()
        minutes = session.minutes_from_midnight_et

        if session_iso != self._session_date:
            if self._session_date is not None and self._session_high > self._session_low:
                self._adx_value = self._adx.update(
                    self._session_high, self._session_low, self._session_close
                )
            self._session_date = session_iso
            self._reset_session()

        self._session_high = max(self._session_high, bar.high)
        self._session_low = min(self._session_low, bar.low)
        self._session_close = bar.close
        atr = self._atr.update(bar.high, bar.low, bar.close)

        # Build the opening range over [or_start, or_end) on RTH bars.
        if session.is_rth and config.or_start_minutes_et <= minutes < config.or_end_minutes_et:
            self._or_high = bar.high if self._or_high is None else max(self._or_high, bar.high)
            self._or_low = bar.low if self._or_low is None else min(self._or_low, bar.low)

        # Position / pending bookkeeping.
        if self._position_side is not None:
            self._bars_in_trade += 1
        else:
            self._bars_since_exit += 1
        if self._entry_pending:
            self._entry_pending_age += 1
            if self._entry_pending_age > 2:
                self._entry_pending = False
                self._entry_pending_age = 0

        exits: tuple[ExitDecision, ...] = ()
        if self._position_side is not None and self._bars_in_trade >= config.time_stop_bars:
            exits = (
                ExitDecision(
                    timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, reason="time_stop"
                ),
            )

        # --- Breakout / failure detection (runs whenever the OR is frozen) ---
        fade_side: Optional[str] = None
        or_ready = (
            self._or_high is not None
            and self._or_low is not None
            and minutes >= config.or_end_minutes_et
            and session.is_rth
            and atr is not None
        )
        if or_ready:
            threshold = config.breakout_atr_mult * atr
            # upside breakout -> fade SHORT on failure
            if self._up_active:
                self._up_age += 1
            if bar.high > self._or_high:
                if not self._up_active:
                    self._up_active = True
                    self._up_age = 0
                    self._up_extended = False
                if (bar.high - self._or_high) >= threshold:
                    self._up_extended = True
            if self._up_active and bar.close < self._or_high:  # closed back inside
                if self._up_extended and self._up_age <= config.failure_window_bars:
                    fade_side = "short"
                self._up_active = self._up_extended = False
                self._up_age = 0
            elif self._up_active and self._up_age > config.failure_window_bars:
                self._up_active = self._up_extended = False
                self._up_age = 0
            # downside breakout -> fade LONG on failure
            if self._dn_active:
                self._dn_age += 1
            if bar.low < self._or_low:
                if not self._dn_active:
                    self._dn_active = True
                    self._dn_age = 0
                    self._dn_extended = False
                if (self._or_low - bar.low) >= threshold:
                    self._dn_extended = True
            if self._dn_active and bar.close > self._or_low:  # closed back inside
                if self._dn_extended and self._dn_age <= config.failure_window_bars and fade_side is None:
                    fade_side = "long"
                self._dn_active = self._dn_extended = False
                self._dn_age = 0
            elif self._dn_active and self._dn_age > config.failure_window_bars:
                self._dn_active = self._dn_extended = False
                self._dn_age = 0

        in_window = (
            config.entry_start_minutes_et <= minutes < config.entry_end_minutes_et
        )
        flat = self._position_side is None and not self._entry_pending
        if not flat or not in_window or self._bar_index < config.warmup_bars or fade_side is None:
            return StrategyResult(exits=exits)

        failing: Optional[str] = None
        if atr is None:
            failing = "indicator_warmup"
        elif self._adx_value is None:
            failing = "adx_warmup"
        elif self._adx_value >= config.adx_max:
            failing = "adx_trending"
        elif self._bars_since_exit < config.cooldown_bars:
            failing = "cooldown"

        if failing is not None:
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side=fade_side,
                    reason=failing,
                ),
                exits=exits,
            )

        stop_distance = config.stop_atr_mult * atr
        if fade_side == "long":
            stop_price = self._quantize(bar.close - stop_distance)
            target_price = self._quantize(bar.close + config.rr_multiple * stop_distance)
            intent_side = "buy"
        else:
            stop_price = self._quantize(bar.close + stop_distance)
            target_price = self._quantize(bar.close - config.rr_multiple * stop_distance)
            intent_side = "sell"

        self._entry_pending = True
        self._entry_pending_age = 0
        signal = SignalDecision.accepted(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=fade_side,
            reason="opening_range_fade",
            metadata={
                "stop_price": stop_price,
                "target_price": target_price,
                "or_high": self._or_high,
                "or_low": self._or_low,
                "atr": atr,
                "adx": self._adx_value,
            },
        )
        intent = OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=intent_side,
            quantity=config.contracts,
            reason="opening_range_fade",
            metadata={
                "stop_price": stop_price,
                "target_price": target_price,
                "signal_price": bar.close,
            },
        )
        return StrategyResult(signal=signal, order_intents=(intent,), exits=exits)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_opening_range_fade.py -v`
Expected: 5 passed (config + 4 strategy tests).

If a test fails, the tests are the source of truth for behavior — fix the implementation, not the test. The likeliest culprit is the ET-minute mapping of the synthetic bars; verify with `classify_timestamp` that minute 0 = 9:30 ET and minute 40 = 10:10 ET on the chosen date before altering strategy logic.

- [ ] **Step 5: Full suite + commit**

Run: `python3 -m pytest -q` → expected 196 passed, 3 skipped

```bash
git add src/full_python/strategy/opening_range_fade.py tests/test_opening_range_fade.py
git commit -m "feat: OpeningRangeFadeStrategy -- failed-OR-breakout fade (MR variant 2)"
```

---

### Task 3: register the strategy for running

**Files:**
- Modify: `src/full_python/cli.py` (imports, `build_strategy`, argparse choices/help)
- Test: `tests/test_opening_range_fade.py` (append one wiring test)

**Interfaces:**
- Consumes: `build_strategy(strategy_name)` from `cli.py` — returns `(config, strategy)`.
- Produces: `build_strategy("opening_range_fade")` returns an `OpeningRangeFadeConfig` + `OpeningRangeFadeStrategy`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_opening_range_fade.py`:

```python
def test_cli_build_strategy_registers_opening_range_fade() -> None:
    from full_python.cli import build_strategy
    from full_python.strategy.opening_range_fade import OpeningRangeFadeStrategy

    config, strategy = build_strategy("opening_range_fade")
    assert isinstance(strategy, OpeningRangeFadeStrategy)
    assert isinstance(config, OpeningRangeFadeConfig)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_opening_range_fade.py::test_cli_build_strategy_registers_opening_range_fade -v`
Expected: FAIL — `ValueError: Unknown strategy: opening_range_fade`

- [ ] **Step 3: Wire it into cli.py**

In `src/full_python/cli.py`, add imports beside the vwap imports:

```python
from full_python.strategy.opening_range_fade import OpeningRangeFadeStrategy
from full_python.strategy.opening_range_fade_config import OpeningRangeFadeConfig
```

Add a branch in `build_strategy` (immediately before the final `raise`):

```python
    if strategy_name == "opening_range_fade":
        config = OpeningRangeFadeConfig()
        return config, OpeningRangeFadeStrategy(config)
```

Update the argparse `choices` list and help string to include `opening_range_fade`:

```python
        choices=["baseline", "adaptive_trend", "adaptive_trend_am", "vwap_reversion", "opening_range_fade"],
        help="... ; vwap_reversion = MR variant 1 (v0.2); opening_range_fade = MR variant 2 (v1)",
```

(Preserve the existing help text; append the `opening_range_fade` clause.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_opening_range_fade.py -v`
Expected: 6 passed

- [ ] **Step 5: Full suite + commit**

Run: `python3 -m pytest -q` → expected 197 passed, 3 skipped

```bash
git add src/full_python/cli.py tests/test_opening_range_fade.py
git commit -m "feat: register opening_range_fade strategy in the CLI"
```

---

## Post-merge (controller, not a task): run the experiment

After merge, run MR variant-2 run-1 on the pre-registered train window and score it against the hypothesis's pre-set criteria (PF ≥ 1.2 with |t| ≥ 2.0, WR, R-multiple distribution, daily correlation with AT). Holdout touched only if train shows an edge. Write the run-1 result doc in `docs/research/`. That analysis is a separate step from this build.

## Not in this plan

- The run itself, its scoring, and the run-1 verdict (post-merge analysis).
- Runs 2/3 (design fixes if the mechanism warrants, per the contract's 3-run budget).
- AM scaling (contract: evaluate flat first).
