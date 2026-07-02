# Exit Branch Sweep And Dollar Equivalents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MNQ/NQ dollar scale explicit in reports and add a reusable sweep command for the MFE trailing plus fresh-breakout branch.

**Architecture:** Extend `trade_analysis.json` with `dollar_equivalents` for MNQ and NQ based on the same point P&L path. Add `full_python.execution.sweeps` and a `sweep-exit-branch` CLI command that loads bars, applies the existing simulator over a parameter grid, and writes ranked JSON/CSV outputs.

**Tech Stack:** Python dataclasses, CSV/JSON outputs, pytest, existing simulator and reporting modules.

---

### Task 1: Dollar Equivalents

**Files:**
- Modify: `src/full_python/reporting/trade_analysis.py`
- Test: `tests/test_trade_analysis.py`

- [x] Add failing test for MNQ/NQ equivalent P&L and drawdown.
- [x] Add `dollar_equivalents` to `trade_analysis.json`.
- [x] Keep commission model explicit as `same_commission_dollars_as_trade_ledger`.
- [x] Run `python3 -m pytest tests/test_trade_analysis.py tests/test_cli_trade_analysis.py -q`.

### Task 2: Sweep Module

**Files:**
- Create: `src/full_python/execution/sweeps.py`
- Test: `tests/test_exit_sweep.py`

- [x] Add failing test for ranked sweep results.
- [x] Add `ExitSweepConfig`.
- [x] Run each parameter combo through the existing simulator.
- [x] Rank by net P&L, then robustness.
- [x] Run `python3 -m pytest tests/test_exit_sweep.py -q`.

### Task 3: Sweep CLI

**Files:**
- Modify: `src/full_python/cli.py`
- Test: `tests/test_cli_exit_sweep.py`

- [x] Add failing CLI test for `sweep-exit-branch`.
- [x] Write `sweep_results.json`.
- [x] Write `sweep_results.csv`.
- [x] Run `python3 -m pytest tests/test_cli_exit_sweep.py tests/test_exit_sweep.py -q`.

### Task 4: Real Smoke And Docs

**Files:**
- Modify: `README.md`
- Create: `docs/runs/2026-07-01-exit-branch-small-sweep.md`

- [x] Run small real sweep: activation 30,40; giveback 20,30; clearance 0; cooldown 0.
- [x] Regenerate 40/20 fresh-breakout analysis with dollar equivalents.
- [x] Document findings.
- [ ] Run `python3 -m pytest -q`.
- [ ] Commit and push.
