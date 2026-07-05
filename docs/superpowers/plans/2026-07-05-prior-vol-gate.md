# Prior-Session-Volatility Entry Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional, off-by-default entry gate to `AdaptiveTrendStrategy` that blocks all entries for a session when the prior session's realized volatility exceeds a fixed, train-calibrated threshold — built as a real, production-quality feature so a future Gate 1 promotion pass requires no rework.

**Architecture:** Self-contained inside `strategy/adaptive_trend_config.py` and `strategy/adaptive_trend.py`. The strategy tracks its own current-session RTH closes and finalizes a realized-vol value at each session boundary (duplicating `regime.py`'s formula, not importing it — no new dependency, no changes to `simulation/engine.py` or the `risk/` package). A small helper method makes the gate's veto decision directly unit-testable, following the file's existing pattern of small `_foo_failing`-style gate methods.

**Tech Stack:** Python 3, `pytest`, stdlib `math`/`statistics`. No new dependencies.

## Global Constraints

- Full design spec: `docs/superpowers/specs/2026-07-05-prior-vol-gate-design.md`. Read it for the "why" — this plan implements it exactly, task boundaries match its architecture section.
- `enable_prior_vol_gate` defaults to `False` and `prior_vol_high_threshold` defaults to the exact train-calibrated value `0.0004638315483775433` (the high-tercile boundary of `prior_realized_vol` computed via `full_python.regime._tercile_bounds` over ONLY the Gate 1 train window, 2023-01-01→2025-06-30, 642 sessions — see `docs/decisions/2026-07-05-gate1-phase2-diagnosis.md`). Do not recompute or adjust this value in this plan; it is fixed by design to avoid lookahead.
- No changes to `regime.py`, `simulation/engine.py`, `replay.py`, or any file under `src/full_python/risk/`.
- No changes to `regime.py`'s tercile/vol-calc functions — the strategy duplicates the formula independently (stdev of log returns over the prior session's RTH minute closes, `>=30` observations required), verified by a parity test against the identical formula computed independently in the test file (using `statistics.pstdev`, not `regime.py`, so the parity check doesn't just re-run the same code against itself).
- `python3 -m pytest -q` must stay green (126 passed, 2 skipped baseline — `FULL_PYTHON_BASELINE_DATA` unset in a plain run) after every task; each task adds tests, never removes coverage.
- Every step's code must integrate with the codebase as it exists TODAY — the file line numbers below were verified by direct reads of `src/full_python/strategy/adaptive_trend.py` (477 lines) and `src/full_python/strategy/adaptive_trend_config.py` (72 lines) on `claude/m4-regime`. If a line number has shifted because an earlier task in this plan already edited the file, use the modified content, not the original line number, to locate the insertion point.
- Commit after each task following existing commit style (`feat: ...`), one commit per task.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/full_python/strategy/adaptive_trend_config.py` (modified) | Adds `enable_prior_vol_gate` and `prior_vol_high_threshold` fields. |
| `src/full_python/strategy/adaptive_trend.py` (modified) | Adds session-close tracking, `_finalize_prior_session_vol()`, `_prior_vol_gate_failing()`, and wires both into `on_bar()`. |
| `tests/test_adaptive_trend.py` (modified) | New tests for the config defaults, the vol-finalization state machine, the gate decision helper, and the `on_bar` wiring. |

---

### Task 1: Config fields

**Files:**
- Modify: `src/full_python/strategy/adaptive_trend_config.py:56-57` (insert between `dollar_point_value` and `to_dict`)
- Test: `tests/test_adaptive_trend.py`

**Interfaces:**
- Produces (used by Tasks 2 and 3): `AdaptiveTrendConfig.enable_prior_vol_gate: bool` (default `False`), `AdaptiveTrendConfig.prior_vol_high_threshold: float` (default `0.0004638315483775433`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_adaptive_trend.py`, right after the existing `test_config_defaults_are_production_values` function:

```python
def test_config_defaults_include_disabled_prior_vol_gate() -> None:
    config = AdaptiveTrendConfig()

    assert config.enable_prior_vol_gate is False
    assert config.prior_vol_high_threshold == pytest.approx(0.0004638315483775433)
    assert len(config.parameter_hash()) == 64
```

Add `import pytest` to the top of `tests/test_adaptive_trend.py` (the file currently has no `pytest` import — it will be needed by this and later tasks' tests):

```python
import math
import pytest
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_adaptive_trend.py::test_config_defaults_include_disabled_prior_vol_gate -v`
Expected: FAIL with `AttributeError: 'AdaptiveTrendConfig' object has no attribute 'enable_prior_vol_gate'`

- [ ] **Step 3: Add the config fields**

In `src/full_python/strategy/adaptive_trend_config.py`, insert immediately after the `dollar_point_value` line and before `def to_dict`:

```python
    dollar_point_value: float = 20.0  # must match the engine's point_value
    enable_prior_vol_gate: bool = False
    # Train-calibrated high-tercile boundary of prior_realized_vol (stdev
    # of log returns over the PRIOR completed RTH session's 1-minute
    # closes, >=30 observations required). Derived from
    # full_python.regime._tercile_bounds over ONLY the Gate 1 train
    # window (2023-01-01 -> 2025-06-30, 642 sessions with enough prior
    # data) -- see docs/decisions/2026-07-05-gate1-phase2-diagnosis.md.
    # Fixed deliberately, not recomputed dynamically, to avoid lookahead
    # into holdout/live data. Re-derive only if the train window itself
    # is redefined.
    prior_vol_high_threshold: float = 0.0004638315483775433
```

(This replaces the single existing `dollar_point_value` line with itself plus the two new fields — the surrounding `to_dict`/`parameter_hash` methods are unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_adaptive_trend.py::test_config_defaults_include_disabled_prior_vol_gate -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test_adaptive_trend.py file to confirm no regressions**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_adaptive_trend.py -v`
Expected: all pass, including the pre-existing `test_config_defaults_are_production_values` (unaffected — it doesn't assert on the total field count)

- [ ] **Step 6: Run full suite**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: 127 passed, 2 skipped (126 existing + 1 new)

- [ ] **Step 7: Commit**

```bash
git add src/full_python/strategy/adaptive_trend_config.py tests/test_adaptive_trend.py
git commit -m "feat: add prior-session-volatility gate config fields (default off)"
```

---

### Task 2: Session-close tracking and vol finalization

**Files:**
- Modify: `src/full_python/strategy/adaptive_trend.py:92` (add state to `__init__`)
- Modify: `src/full_python/strategy/adaptive_trend.py:124-128` (wire into `on_bar`'s session-boundary block)
- Modify: `src/full_python/strategy/adaptive_trend.py:267-280` (add `_finalize_prior_session_vol` after `_dll_safe_quantity`)
- Test: `tests/test_adaptive_trend.py`

**Interfaces:**
- Consumes: `full_python.data.sessions.SessionInfo.is_rth` (existing, `data/sessions.py:34`); `full_python.models.MarketBar.close` (existing).
- Produces (used by Task 3): `AdaptiveTrendStrategy._current_session_rth_closes: list[float]`, `AdaptiveTrendStrategy._prior_session_realized_vol: Optional[float]`, `AdaptiveTrendStrategy._finalize_prior_session_vol() -> None`.

This task does NOT wire anything into the entry-gating decision yet — it only makes the strategy correctly track and finalize the vol value across session boundaries during `on_bar`. Task 3 wires the veto.

- [ ] **Step 1: Write the failing tests**

Add `import statistics` to the top of `tests/test_adaptive_trend.py`, alongside the other stdlib imports (after Task 1's `import pytest` line):

```python
import math
import pytest
import statistics
from datetime import datetime, timedelta, timezone
```

Then add these tests to `tests/test_adaptive_trend.py`:

```python
def test_finalize_prior_session_vol_matches_independently_computed_stdev() -> None:
    closes = [20000.0 + i * 0.37 for i in range(35)]
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    strategy._current_session_rth_closes = list(closes)

    strategy._finalize_prior_session_vol()

    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    expected = statistics.pstdev(returns)
    assert strategy._prior_session_realized_vol == pytest.approx(expected)
    assert strategy._current_session_rth_closes == []


def test_finalize_prior_session_vol_leaves_value_unchanged_when_insufficient_data() -> None:
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    strategy._prior_session_realized_vol = 0.0002  # a previously-computed value
    strategy._current_session_rth_closes = [20000.0 + i for i in range(10)]  # only 10 < 30

    strategy._finalize_prior_session_vol()

    assert strategy._prior_session_realized_vol == 0.0002
    assert strategy._current_session_rth_closes == []


def test_finalize_prior_session_vol_stays_none_on_cold_start() -> None:
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    strategy._current_session_rth_closes = [20000.0, 20001.0]

    strategy._finalize_prior_session_vol()

    assert strategy._prior_session_realized_vol is None


def _rth_bars_for_session(day: int, closes: list[float]) -> list[MarketBar]:
    """One RTH-only session's worth of 1-minute bars starting 9:30 ET."""
    base = datetime(2026, 6, 29 + day, 13, 30, tzinfo=timezone.utc)  # 9:30 ET
    bars = []
    prev_close = closes[0] - 1.0
    for minute, close in enumerate(closes):
        timestamp = (base + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bars.append(
            MarketBar(
                timestamp_utc=timestamp,
                symbol="NQU2026",
                open=prev_close,
                high=max(prev_close, close) + 0.5,
                low=min(prev_close, close) - 0.5,
                close=close,
                volume=100.0,
            )
        )
        prev_close = close
    return bars


def test_on_bar_accumulates_rth_closes_and_finalizes_vol_at_session_boundary() -> None:
    session1_closes = [20000.0 + i * 0.37 for i in range(35)]
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())

    for bar in _rth_bars_for_session(0, session1_closes):
        strategy.on_bar(bar)
    assert strategy._prior_session_realized_vol is None  # not finalized until session 2 starts
    assert len(strategy._current_session_rth_closes) == 35

    session2_bars = _rth_bars_for_session(1, [21000.0, 21001.0])
    strategy.on_bar(session2_bars[0])  # first bar of session 2 triggers the transition

    returns = [
        math.log(session1_closes[i] / session1_closes[i - 1]) for i in range(1, 35)
    ]
    expected = statistics.pstdev(returns)
    assert strategy._prior_session_realized_vol == pytest.approx(expected)
    assert strategy._current_session_rth_closes == [21000.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_adaptive_trend.py -k "finalize_prior_session_vol or on_bar_accumulates" -v`
Expected: FAIL — `AttributeError: 'AdaptiveTrendStrategy' object has no attribute '_current_session_rth_closes'` (or `_finalize_prior_session_vol`)

- [ ] **Step 3: Add the strategy state and method**

In `src/full_python/strategy/adaptive_trend.py`, in `__init__`, insert immediately after `self._daily_limit_hit = False`:

```python
        self._daily_limit_hit = False
        # Prior-session-volatility gate state. Duplicates
        # full_python.regime.compute_session_features's
        # prior_realized_vol formula (not imported -- see the design
        # spec at docs/superpowers/specs/2026-07-05-prior-vol-gate-design.md);
        # a parity test in test_adaptive_trend.py guards against drift.
        self._current_session_rth_closes: list[float] = []
        self._prior_session_realized_vol: Optional[float] = None
```

In `on_bar`, replace the session-boundary block:

```python
        session = classify_timestamp(bar.timestamp_utc)
        session_iso = session.session_date.isoformat()
        if session_iso != self._session_date:
            self._session_date = session_iso
            self._reset_break_state()
```

with:

```python
        session = classify_timestamp(bar.timestamp_utc)
        session_iso = session.session_date.isoformat()
        if session_iso != self._session_date:
            self._finalize_prior_session_vol()
            self._session_date = session_iso
            self._reset_break_state()
        if session.is_rth:
            self._current_session_rth_closes.append(bar.close)
```

Add the new method after `_dll_safe_quantity` (which currently ends with `return max(0, min(desired_qty, max_safe_qty))`, right before the `# S/R break detection + prove-it` section comment):

```python
    # ------------------------------------------------------------------
    # Prior-session-volatility gate (measurement duplicated from
    # full_python.regime.compute_session_features; see the design spec
    # at docs/superpowers/specs/2026-07-05-prior-vol-gate-design.md)
    # ------------------------------------------------------------------

    def _finalize_prior_session_vol(self) -> None:
        """Compute realized vol from the session just completed, to gate
        the upcoming session's entries. Leaves the previous value
        unchanged (None on cold start) when there isn't enough data --
        never fabricates a value from too little history.
        """
        closes = self._current_session_rth_closes
        if len(closes) >= 30:
            returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
            mean = sum(returns) / len(returns)
            self._prior_session_realized_vol = math.sqrt(
                sum((r - mean) ** 2 for r in returns) / len(returns)
            )
        self._current_session_rth_closes = []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_adaptive_trend.py -k "finalize_prior_session_vol or on_bar_accumulates" -v`
Expected: 4 passed

- [ ] **Step 5: Run the full existing test_adaptive_trend.py file to confirm no regressions**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_adaptive_trend.py -v`
Expected: all pass — in particular `test_full_simulation_smoke_respects_window_and_stop_bounds` must still pass unchanged, since the new state is purely additive and the gate isn't wired into any decision yet

- [ ] **Step 6: Run full suite**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: 131 passed, 2 skipped (127 + 4 new)

- [ ] **Step 7: Commit**

```bash
git add src/full_python/strategy/adaptive_trend.py tests/test_adaptive_trend.py
git commit -m "feat: track session RTH closes and finalize prior-session realized vol"
```

---

### Task 3: Gate decision and wiring into `on_bar`

**Files:**
- Modify: `src/full_python/strategy/adaptive_trend.py:194-196` (wire into the failing-gate chain, same pattern as the existing `daily_limit_halt` check)
- Modify: `src/full_python/strategy/adaptive_trend.py` (add `_prior_vol_gate_failing` next to `_finalize_prior_session_vol`, added in Task 2)
- Test: `tests/test_adaptive_trend.py`

**Interfaces:**
- Consumes: `AdaptiveTrendConfig.enable_prior_vol_gate`, `AdaptiveTrendConfig.prior_vol_high_threshold` (Task 1); `AdaptiveTrendStrategy._prior_session_realized_vol` (Task 2).
- Produces: `AdaptiveTrendStrategy._prior_vol_gate_failing() -> Optional[str]` — returns `"prior_vol_gate"` when the gate should block, else `None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_adaptive_trend.py`:

```python
def test_prior_vol_gate_failing_blocks_when_enabled_and_above_threshold() -> None:
    config = AdaptiveTrendConfig(enable_prior_vol_gate=True, prior_vol_high_threshold=0.0005)
    strategy = AdaptiveTrendStrategy(config)
    strategy._prior_session_realized_vol = 0.0006

    assert strategy._prior_vol_gate_failing() == "prior_vol_gate"


def test_prior_vol_gate_failing_allows_when_enabled_and_below_threshold() -> None:
    config = AdaptiveTrendConfig(enable_prior_vol_gate=True, prior_vol_high_threshold=0.0005)
    strategy = AdaptiveTrendStrategy(config)
    strategy._prior_session_realized_vol = 0.0003

    assert strategy._prior_vol_gate_failing() is None


def test_prior_vol_gate_failing_allows_when_disabled_even_if_above_threshold() -> None:
    config = AdaptiveTrendConfig(enable_prior_vol_gate=False, prior_vol_high_threshold=0.0005)
    strategy = AdaptiveTrendStrategy(config)
    strategy._prior_session_realized_vol = 0.0006  # would trigger if enabled

    assert strategy._prior_vol_gate_failing() is None


def test_prior_vol_gate_failing_allows_on_cold_start_with_no_prior_vol_yet() -> None:
    config = AdaptiveTrendConfig(enable_prior_vol_gate=True, prior_vol_high_threshold=0.0005)
    strategy = AdaptiveTrendStrategy(config)
    # self._prior_session_realized_vol defaults to None (cold start)

    assert strategy._prior_vol_gate_failing() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_adaptive_trend.py -k "prior_vol_gate_failing" -v`
Expected: FAIL with `AttributeError: 'AdaptiveTrendStrategy' object has no attribute '_prior_vol_gate_failing'`

- [ ] **Step 3: Add the gate method and wire it in**

In `src/full_python/strategy/adaptive_trend.py`, add this method directly after `_finalize_prior_session_vol` (added in Task 2), inside the same `# Prior-session-volatility gate` section:

```python
    def _prior_vol_gate_failing(self) -> Optional[str]:
        """Session-level veto: block entries when the prior session's
        realized vol exceeded the train-calibrated high-tercile
        threshold. See adaptive_trend_config.py's
        prior_vol_high_threshold docstring for how the threshold was
        derived. Returns None (never blocks) until the gate is enabled
        and at least one prior session's vol has been computed.
        """
        if (
            self.config.enable_prior_vol_gate
            and self._prior_session_realized_vol is not None
            and self._prior_session_realized_vol > self.config.prior_vol_high_threshold
        ):
            return "prior_vol_gate"
        return None
```

In `on_bar`, find this existing block:

```python
        if failing is None and config.enable_daily_loss_limit and self._daily_limit_hit:
            failing = "daily_limit_halt"
```

and add immediately after it:

```python
        if failing is None and config.enable_daily_loss_limit and self._daily_limit_hit:
            failing = "daily_limit_halt"
        if failing is None:
            failing = self._prior_vol_gate_failing()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_adaptive_trend.py -k "prior_vol_gate_failing" -v`
Expected: 4 passed

- [ ] **Step 5: Run the full existing test_adaptive_trend.py file — this is the regression-safety proof**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_adaptive_trend.py -v`
Expected: all pass. In particular, `test_full_simulation_smoke_respects_window_and_stop_bounds` runs `AdaptiveTrendStrategy(AdaptiveTrendConfig())` — the default config has `enable_prior_vol_gate=False`, so `_prior_vol_gate_failing()` always returns `None` for that test regardless of any internal vol state, proving the new gate is fully inert until explicitly enabled.

- [ ] **Step 6: Run the AM/DLL and simulation-engine test files**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest tests/test_am_dll.py tests/test_simulation_engine.py -v`
Expected: all pass, unchanged — these exercise the strategy through `SimulationEngine` with the default (gate-off) config and must show zero behavioral difference.

- [ ] **Step 7: Run full suite**

Run: `cd "/Users/sais/Documents/New Beginning/full-python" && python3 -m pytest -q`
Expected: 135 passed, 2 skipped (131 + 4 new)

- [ ] **Step 8: Commit**

```bash
git add src/full_python/strategy/adaptive_trend.py tests/test_adaptive_trend.py
git commit -m "feat: wire the prior-session-volatility gate into entry evaluation"
```

---

## Self-Review Notes

- **Spec coverage:** All of the design spec's "Architecture" section is covered — config additions (Task 1), strategy state + finalization (Task 2), gating check (Task 3). The spec's "Testing" section items 1-4 are covered by Tasks 2-3's tests; item 5 (full-backtest train/holdout comparison) and the "Evaluation path" section are explicitly out of scope for this plan, per the spec's own "Explicitly out of scope" list — that is a follow-up analysis step using this feature, not part of building it.
- **No placeholders:** every code block is complete, matches the actual current file contents (verified via direct reads of both files on `claude/m4-regime`), and every test has real assertions.
- **Type/signature consistency:** `_prior_session_realized_vol: Optional[float]`, `_current_session_rth_closes: list[float]`, `_finalize_prior_session_vol() -> None`, `_prior_vol_gate_failing() -> Optional[str]` are used identically across Tasks 2 and 3. `enable_prior_vol_gate: bool` and `prior_vol_high_threshold: float` (Task 1) match their usage in Task 3's gate method exactly.
