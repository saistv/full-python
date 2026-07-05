# Execution Core Foundations (Plan A of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Behavior-preserving refactors that make the live execution core possible: `RiskManager` decoupled from `SimulationConfig` via a `RiskLimits` dataclass, and the engine's position/fill lifecycle extracted into a shared `PositionEngine` that the future PaperBroker reuses (identity by shared code — approved Option A).

**Architecture:** Two mechanical extractions with a zero-behavior-change contract. Proof of correctness is the untouched test suite: **no test file is modified in this plan, and all 155 tests must pass identically.** Plan B (broker protocol, state machine, PaperBroker, supervisor, LiveLoop) follows after this merges and the real-data golden tests are re-verified on the main clone.

**Tech Stack:** Python 3 stdlib only. Touched: `src/full_python/risk/`, `src/full_python/simulation/`.

## Global Constraints

- **Zero behavior change.** No test file is created, modified, or deleted in this plan. `python3 -m pytest -q` must report exactly **155 passed, 2 skipped** after every task. Any test failure means the refactor broke behavior — fix the refactor, never the test.
- No changes to `strategy/`, `regime.py`, `research/`, `reporting/`, `data/`, `cli.py`, `models.py`, `events.py`.
- Public APIs preserved: `SimulationEngine(config).run(bars, strategy, *, ledger=None) -> SimulationResult` unchanged; `RiskManager.veto_reason(...)` keyword signature unchanged.
- After Task 1: `grep -rn "SimulationConfig" src/full_python/risk/` returns nothing.
- Commit style `refactor: ...`.

---

### Task 1: RiskLimits — decouple risk/ from simulation/

**Files:**
- Create: `src/full_python/risk/limits.py`
- Modify: `src/full_python/risk/risk_manager.py` (imports, `__init__`, the three `self.config.*` reads)
- Modify: `src/full_python/simulation/engine.py:88` (the `RiskManager(config)` construction)

**Interfaces:**
- Consumes: nothing new. `RiskManager` currently reads exactly three fields off `SimulationConfig`: `max_contracts` (risk_manager.py:56), `flatten_minutes_et` (risk_manager.py:68, a derived property = `flatten_hour_et*60 + flatten_minute_et`), `rth_entries_only` (risk_manager.py:72).
- Produces (Plan B relies on this): `RiskLimits(max_contracts: int, flatten_minutes_et: int, rth_entries_only: bool)` frozen dataclass in `full_python.risk.limits`; `RiskManager.__init__(limits: RiskLimits)`.

Note: the design spec placed this file at `execution/limits.py`; it lives in `risk/limits.py` instead so the risk package owns its own config type and never imports from `execution/` (layering: execution → risk, never the reverse). The spec is amended alongside this plan.

- [ ] **Step 1: Create the dataclass**

Create `src/full_python/risk/limits.py`:

```python
"""Broker- and engine-agnostic limits consumed by RiskManager.

Extracted from the three SimulationConfig fields RiskManager actually
reads, so the risk layer no longer imports simulation internals and a
live execution engine (Gate 5+) can construct limits without a
SimulationConfig. See docs/superpowers/specs/2026-07-05-execution-core-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    max_contracts: int
    flatten_minutes_et: int  # minutes from midnight ET (e.g. 15:59 -> 959)
    rth_entries_only: bool
```

- [ ] **Step 2: Rewire RiskManager**

In `src/full_python/risk/risk_manager.py`:
- Delete the line `from full_python.simulation.config import SimulationConfig`
- Add `from full_python.risk.limits import RiskLimits`
- Change the constructor to:

```python
class RiskManager:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits
```

- Replace the three reads: `self.config.max_contracts` → `self.limits.max_contracts`, `self.config.flatten_minutes_et` → `self.limits.flatten_minutes_et`, `self.config.rth_entries_only` → `self.limits.rth_entries_only`. (These are the only `self.config` uses — verify with `grep -n "self.config" src/full_python/risk/risk_manager.py` before and after: before shows exactly 3 hits, after shows 0.)
- Update the module docstring's extraction note to mention the RiskLimits decoupling (one sentence appended, e.g. "Decoupled from SimulationConfig via risk.limits.RiskLimits (2026-07-05); SimulationEngine constructs the limits at init.").

- [ ] **Step 3: Rewire the engine construction site**

In `src/full_python/simulation/engine.py`, add `from full_python.risk.limits import RiskLimits` to the imports, and change `SimulationEngine.__init__`:

```python
    def __init__(self, config: SimulationConfig) -> None:
        self.config = config
        self._risk_manager = RiskManager(
            RiskLimits(
                max_contracts=config.max_contracts,
                flatten_minutes_et=config.flatten_minutes_et,
                rth_entries_only=config.rth_entries_only,
            )
        )
```

- [ ] **Step 4: Prove zero behavior change**

Run: `python3 -m pytest -q`
Expected: exactly `155 passed, 2 skipped`

Run: `grep -rn "SimulationConfig" src/full_python/risk/`
Expected: no output

- [ ] **Step 5: Commit**

```bash
git add src/full_python/risk/limits.py src/full_python/risk/risk_manager.py src/full_python/simulation/engine.py
git commit -m "refactor: decouple RiskManager from SimulationConfig via RiskLimits"
```

---

### Task 2: PositionEngine — extract the position/fill lifecycle from SimulationEngine

**Files:**
- Create: `src/full_python/simulation/position_engine.py`
- Modify: `src/full_python/simulation/engine.py` (becomes a thin bar loop delegating to PositionEngine)
- Modify: `src/full_python/simulation/__init__.py` only if it re-exports engine internals (check first; likely no change)

**Interfaces:**
- Consumes: `RiskLimits`/`RiskManager` as wired by Task 1; everything `engine.py` already imports (`Fill`, `Trade`, `EventLedger`/`EventType`, `SessionInfo`, `is_daily_loss_breached`, `FILL_TIMING_*`).
- Produces (Plan B's PaperBroker builds directly on this):

```python
class PositionEngine:
    def __init__(self, config: SimulationConfig, strategy: Strategy, ledger: EventLedger) -> None: ...
    # per-bar, called in this exact order by any driver (sim or live loop):
    def process_pre_strategy(self, bar: MarketBar, session: SessionInfo) -> float: ...
        # runs, in the engine's exact current order:
        # _flatten_if_session_changed -> _process_open_gap_stop ->
        # _process_pending_entry -> _process_pending_exit ->
        # _update_excursions -> _process_intrabar_stop_and_target ->
        # _process_backstop_flatten -> _check_daily_loss_limit
        # returns session_pnl (for strategy.on_bar_context)
    def apply_strategy_result(self, bar: MarketBar, session: SessionInfo, result: StrategyResult) -> None: ...
        # the current _record_strategy_result verbatim (ledger events,
        # risk veto via RiskManager, pending-entry/exit creation,
        # signal-bar-close immediate fills)
    def close_end_of_data(self) -> None: ...
    @property
    def trades(self) -> list[Trade]: ...
    @property
    def daily_limit_hit(self) -> bool: ...
    @property
    def position(self): ...          # Optional[_Position]
    @property
    def previous_bar(self): ...      # Optional[MarketBar]
```

**This is a MOVE, not a rewrite.** The bodies of `_flatten_if_session_changed`, `_process_open_gap_stop`, `_process_pending_entry`, `_process_pending_exit`, `_update_excursions`, `_process_intrabar_stop_and_target`, `_process_backstop_flatten`, `_check_daily_loss_limit`, `_record_strategy_result`, `_close_at_end_of_data`, `_veto_reason`, `_reference_price`, `_open_position`, `_close_position` (engine.py:135-604) move to `position_engine.py` **byte-identical except for mechanical renames**: `state.X` → `self._X` (the `_State` dataclass's fields become PositionEngine instance attributes; `_State`, `_Position`, `_PendingEntry`, `_PendingExit` move to position_engine.py, with `_State.strategy` replaced by a `self._strategy` set in `__init__`), `self.config` stays `self.config`, `ledger` parameters become `self._ledger`. Every ledger event type, payload, timestamp source, fill price formula, hook call (`on_fill`, `on_trade_closed` via `getattr`), and the engine.py:446-472 veto NOTE comment move unchanged. Do not "improve" anything — the diff reviewers will compare moved bodies against the originals.

- [ ] **Step 1: Create position_engine.py with the moved code**

Structure of the new file:

```python
"""Position/fill lifecycle shared by SimulationEngine and (Gate 5+) the
paper broker -- identity by shared code, never by parallel
reimplementation. Behavior-preserving extraction from
simulation/engine.py (2026-07-05); the proof is the unchanged test
suite. See docs/superpowers/specs/2026-07-05-execution-core-design.md.
"""
from __future__ import annotations
# imports: exactly what the moved bodies need (copy from engine.py)


# _Position, _PendingEntry, _PendingExit move here unchanged


class PositionEngine:
    def __init__(self, config: SimulationConfig, strategy: Strategy, ledger: EventLedger) -> None:
        self.config = config
        self._strategy = strategy
        self._ledger = ledger
        self._risk_manager = RiskManager(RiskLimits(
            max_contracts=config.max_contracts,
            flatten_minutes_et=config.flatten_minutes_et,
            rth_entries_only=config.rth_entries_only,
        ))
        # former _State fields:
        self._position = None
        self._pending_entry = None
        self._pending_exit = None
        self._previous_bar = None
        self._previous_session = None
        self._trades: list[Trade] = []
        self._cumulative_net_pnl = 0.0
        self._session_start_pnl = 0.0
        self._daily_limit_hit = False

    def process_pre_strategy(self, bar, session):
        self._flatten_if_session_changed(session)
        self._process_open_gap_stop(bar)
        self._process_pending_entry(bar, session)
        self._process_pending_exit(bar)
        self._update_excursions(bar)
        self._process_intrabar_stop_and_target(bar)
        self._process_backstop_flatten(bar, session)
        return self._check_daily_loss_limit(bar)

    def note_bar_processed(self, bar, session):
        self._previous_bar = bar
        self._previous_session = session

    # apply_strategy_result, close_end_of_data, properties, and all the
    # moved private methods follow -- bodies verbatim from engine.py
    # with the state./ledger renames described above.
```

`note_bar_processed` carries the engine loop's current `state.previous_bar = bar; state.previous_session = session` tail — the driver calls it last, after `apply_strategy_result`, preserving the exact current sequence.

- [ ] **Step 2: Rewrite SimulationEngine.run as the thin driver**

`engine.py` keeps: `SimulationConfig` import, `SimulationResult`, and this loop (replacing everything from `_State` down — those definitions move out):

```python
class SimulationEngine:
    def __init__(self, config: SimulationConfig) -> None:
        self.config = config

    def run(self, bars, strategy, *, ledger=None):
        active_ledger = EventLedger() if ledger is None else ledger
        engine = PositionEngine(self.config, strategy, active_ledger)
        session_dates: list[str] = []

        for bar in bars:
            session = classify_timestamp(bar.timestamp_utc)
            session_iso = session.session_date.isoformat()
            if not session_dates or session_dates[-1] != session_iso:
                session_dates.append(session_iso)
            active_ledger.append(
                EventType.BAR, timestamp_utc=bar.timestamp_utc, payload=bar.to_payload()
            )

            session_pnl = engine.process_pre_strategy(bar, session)

            on_bar_context = getattr(strategy, "on_bar_context", None)
            if on_bar_context is not None:
                on_bar_context(session_pnl=session_pnl, daily_limit_hit=engine.daily_limit_hit)
            result = strategy.on_bar(bar)
            engine.apply_strategy_result(bar, session, result)
            engine.note_bar_processed(bar, session)

        engine.close_end_of_data()
        return SimulationResult(
            ledger=active_ledger,
            trades=tuple(engine.trades),
            session_dates=tuple(session_dates),
        )
```

(Adjust keyword/typing details to match the current file exactly; the Task 1 `RiskManager` construction in `SimulationEngine.__init__` is DELETED here — it now lives inside `PositionEngine.__init__`.)

- [ ] **Step 3: Prove zero behavior change**

Run: `python3 -m pytest -q`
Expected: exactly `155 passed, 2 skipped` — with `git status` showing NO test files touched.

Run: `python3 -m pytest tests/test_simulation_engine.py tests/test_am_dll.py tests/test_adaptive_trend.py -v 2>&1 | tail -5`
Expected: all pass (these are the suites that exercise the moved logic hardest: scripted fills, DLL halts, AM sizing, backstop flatten).

- [ ] **Step 4: Commit**

```bash
git add src/full_python/simulation/position_engine.py src/full_python/simulation/engine.py
git commit -m "refactor: extract PositionEngine -- shared position/fill lifecycle"
```

---

## Post-merge verification (controller step, not a task)

After this plan merges, on the main clone (where the gitignored dataset exists): run the full suite INCLUDING the golden-trade tests un-skipped, i.e. `python3 -m pytest -q` with `runs/` data present, and confirm the two previously-skipped tests now run and pass — the real-data proof that the extraction preserved every fill. Plan B does not start until this is green.

## Not in this plan (Plan B, after post-merge verification)

- `execution/` package: broker protocol, order state machine, PaperBroker (a `Broker` facade over `PositionEngine`), risk supervisor, LiveLoop, and the LiveLoop==SimulationEngine identity tests.
