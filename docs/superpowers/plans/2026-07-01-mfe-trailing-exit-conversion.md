# MFE Trailing Exit Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a research-only MFE trailing exit that converts favorable movement into realized exits without changing entry logic.

**Architecture:** Extend the existing trade simulator with an optional `ExitConversionConfig`. The initial conversion rule is long-only MFE trailing: after a completed bar reaches an activation threshold, the simulator sets a trailing stop at `best_high - giveback_points`; that trail can exit on later bars with reason `mfe_trailing_stop`. This intentionally avoids same-bar high/low ordering assumptions from 1-minute OHLC.

**Tech Stack:** Python dataclasses, pytest, existing CLI/CSV/JSON report flow.

---

### Task 1: Simulator Exit Conversion

**Files:**
- Modify: `src/full_python/execution/simulator.py`
- Test: `tests/test_execution_simulator.py`

- [ ] Add failing tests for MFE trailing stop activation and later-bar exit.
- [ ] Add `ExitConversionConfig` with `mfe_trailing_activation_points` and `mfe_trailing_giveback_points`.
- [ ] Track trailing stop price inside `_OpenTrade`.
- [ ] Emit `exit_conversion` assumptions into `trade_summary.json`.
- [ ] Run `python3 -m pytest tests/test_execution_simulator.py -q`.

### Task 2: CLI And CSV Fields

**Files:**
- Modify: `src/full_python/cli.py`
- Modify: `src/full_python/execution/simulator.py`
- Test: `tests/test_cli_trade_simulation.py`

- [ ] Add failing CLI test for `--mfe-trailing-activation-points` and `--mfe-trailing-giveback-points`.
- [ ] Wire CLI options to `ExitConversionConfig`.
- [ ] Add `exit_conversion_name` and `trailing_stop_price` to `trades.csv`.
- [ ] Run `python3 -m pytest tests/test_cli_trade_simulation.py tests/test_execution_simulator.py -q`.

### Task 3: Real Smoke And Documentation

**Files:**
- Modify: `README.md`
- Create: `docs/runs/2026-07-01-rth-mfe-trailing-exit-smoke.md`

- [ ] Run a first real smoke with activation `40` and giveback `20` using previous-close roll exits.
- [ ] Run `analyze-trades` on that new ledger.
- [ ] Document baseline-vs-exit-conversion comparison.
- [ ] Run `python3 -m pytest -q`.
- [ ] Commit and push the branch.
