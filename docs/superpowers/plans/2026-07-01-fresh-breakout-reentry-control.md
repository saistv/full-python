# Fresh Breakout Re-Entry Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structure-based re-entry control so a strategy cannot recycle after an exit until price forms and breaks a fresh post-exit high.

**Architecture:** Extend `ReentryControlConfig` with `require_fresh_breakout_after_exit` and `fresh_breakout_clearance_points`. After any exit, the simulator tracks the highest high formed while flat; new long order intents are ignored until a bar closes above that high plus the configured clearance. This keeps entry logic unchanged while adding a research gate for post-exit structure.

**Tech Stack:** Python dataclasses, pytest, existing CLI and real-smoke documentation flow.

---

### Task 1: Simulator Fresh-Breakout Gate

**Files:**
- Modify: `src/full_python/execution/simulator.py`
- Test: `tests/test_execution_simulator.py`

- [x] Add tests for fresh-breakout re-entry blocking and clearance.
- [x] Add config fields and validation to `ReentryControlConfig`.
- [x] Track post-exit highest high while flat.
- [x] Block long order intents until close clears the post-exit high plus clearance.
- [x] Run `python3 -m pytest tests/test_execution_simulator.py -q`.

### Task 2: CLI Flags

**Files:**
- Modify: `src/full_python/cli.py`
- Test: `tests/test_cli_trade_simulation.py`

- [x] Add test for `--require-fresh-breakout-after-exit`.
- [x] Add test coverage for `--fresh-breakout-clearance-points`.
- [x] Wire CLI flags through `run_baseline_trade_simulation`.
- [x] Run `python3 -m pytest tests/test_cli_trade_simulation.py tests/test_execution_simulator.py -q`.

### Task 3: Real Smoke

**Files:**
- Modify: `README.md`
- Create: `docs/runs/2026-07-01-rth-mfe-trailing-fresh-breakout-smoke.md`

- [x] Run MFE trailing 40/20 with fresh-breakout re-entry and previous-close roll exits.
- [x] Run one clearance sensitivity check at 1.0 point.
- [x] Analyze the resulting trade ledgers.
- [x] Document comparison to control, MFE-only, and cooldown runs.
- [ ] Run `python3 -m pytest -q`.
- [ ] Commit and push the branch.
