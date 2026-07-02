# Short Side Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class short-side signal generation, simulation fills, stops, excursions, and MFE trailing support.

**Architecture:** Keep the existing baseline strategy and simulator structure, but make direction explicit. The strategy emits long breakouts and short breakdowns when enabled; the simulator handles long and short math through side-aware helper functions while preserving one open position max.

**Tech Stack:** Python dataclasses, pytest, existing full-python strategy/simulator modules.

---

### Task 1: Strategy Short Signals

**Files:**
- Modify: `src/full_python/strategy/config.py`
- Modify: `src/full_python/strategy/baseline.py`
- Test: `tests/test_baseline_strategy.py`

- [x] Add config flags `enable_long: bool = True` and `enable_short: bool = False`.
- [x] Write a failing test proving a close below prior low emits an accepted short signal and `sell` order.
- [x] Implement symmetric short breakdown logic.
- [x] Run `python3 -m pytest tests/test_baseline_strategy.py -q`.

### Task 2: Simulator Short Fills And Stops

**Files:**
- Modify: `src/full_python/execution/simulator.py`
- Test: `tests/test_execution_simulator.py`

- [x] Write failing tests for short entry, short stop, short slippage/P&L, and short MFE/MAE.
- [x] Implement side-aware order acceptance for `sell`.
- [x] Implement short open/close math and stop detection.
- [x] Run `python3 -m pytest tests/test_execution_simulator.py -q`.

### Task 3: Short MFE Trailing

**Files:**
- Modify: `src/full_python/execution/simulator.py`
- Test: `tests/test_execution_simulator.py`

- [x] Write a failing test for a short trade activating MFE trailing and exiting on a downward trailing stop.
- [x] Make trailing-stop touch/update logic side-aware.
- [x] Run `python3 -m pytest tests/test_execution_simulator.py -q`.

### Task 4: Real Candidate Smoke

**Files:**
- Add: `docs/runs/2026-07-01-short-side-support-smoke.md`

- [x] Run the full test suite.
- [x] Run a real selected-stream simulation with Candidate A settings and both sides enabled.
- [x] Analyze the resulting trades.
- [x] Document headline long/short contribution and whether the new short side helps or hurts.
