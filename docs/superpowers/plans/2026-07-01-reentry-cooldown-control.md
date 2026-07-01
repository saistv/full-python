# Re-Entry Cooldown Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add simulator-level re-entry discipline so exit-conversion research can control churn after stops, trailing exits, symbol changes, and end-of-position events.

**Architecture:** Add `ReentryControlConfig` to the execution simulator. Every exit blocks same-bar re-entry, and `cooldown_bars_after_exit=N` blocks the next N bars before a new entry can be accepted. This keeps entry logic unchanged while letting research test whether MFE trailing needs structure-level re-entry control.

**Tech Stack:** Python dataclasses, pytest, existing CLI and JSON summary flow.

---

### Task 1: Simulator Re-Entry Cooldown

**Files:**
- Modify: `src/full_python/execution/simulator.py`
- Test: `tests/test_execution_simulator.py`

- [ ] Add failing test that same-bar re-entry is blocked after an exit.
- [ ] Add failing test that `cooldown_bars_after_exit=1` blocks the next bar after an exit.
- [ ] Add `ReentryControlConfig`.
- [ ] Apply re-entry blocking only to new order intents, not to exit processing.
- [ ] Run `python3 -m pytest tests/test_execution_simulator.py -q`.

### Task 2: CLI Cooldown Option

**Files:**
- Modify: `src/full_python/cli.py`
- Test: `tests/test_cli_trade_simulation.py`

- [ ] Add failing CLI test for `--cooldown-bars-after-exit`.
- [ ] Wire CLI option into `ReentryControlConfig`.
- [ ] Include assumptions in `trade_summary.json`.
- [ ] Run `python3 -m pytest tests/test_cli_trade_simulation.py tests/test_execution_simulator.py -q`.

### Task 3: Real Smoke And Docs

**Files:**
- Modify: `README.md`
- Create: `docs/runs/2026-07-01-rth-mfe-trailing-cooldown-smoke.md`

- [ ] Run real smoke with previous-close roll exits, MFE trailing 40/20, and cooldown 10.
- [ ] Analyze the resulting `trades.csv`.
- [ ] Document comparison to MFE trailing without cooldown.
- [ ] Run `python3 -m pytest -q`.
- [ ] Commit and push the branch.
