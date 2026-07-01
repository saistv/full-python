# Roll-Controlled Excursion Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove roll/symbol-change gap profit as a hidden source of edge and add MFE/MAE fields to every simulated trade.

**Architecture:** Extend the simulator with a `symbol_change_exit_mode` option. The existing `next_open` behavior remains the default for backward compatibility, while `previous_close` exits at the last bar close before the symbol changes. Track long-trade MFE/MAE bar-by-bar inside `_OpenTrade`, emit those fields into `trades.csv`, and include stopped-trade excursion metrics in `trade_analysis.json`.

**Tech Stack:** Python dataclasses, pytest, existing CSV/JSON CLI patterns.

---

### Task 1: Simulator Roll Control And MFE/MAE

**Files:**
- Modify: `src/full_python/execution/simulator.py`
- Test: `tests/test_execution_simulator.py`

- [ ] Add a failing test that a long trade tracks max favorable and adverse excursion before stop exit.
- [ ] Add a failing test that `symbol_change_exit_mode="previous_close"` exits the old contract at the previous bar close, not the new contract open.
- [ ] Implement `_OpenTrade` excursion fields and a helper that updates open-trade excursion on each bar.
- [ ] Implement the `symbol_change_exit_mode` argument with allowed values `next_open` and `previous_close`.
- [ ] Run `python3 -m pytest tests/test_execution_simulator.py -q`.

### Task 2: CLI Option And CSV Contract

**Files:**
- Modify: `src/full_python/cli.py`
- Modify: `src/full_python/execution/simulator.py`
- Test: `tests/test_cli_trade_simulation.py`

- [ ] Add a failing CLI test that passes `--symbol-change-exit-mode previous_close` and sees that assumption in `trade_summary.json`.
- [ ] Wire the CLI option through `run_baseline_trade_simulation`.
- [ ] Ensure `trades.csv` includes `max_favorable_excursion_points` and `max_adverse_excursion_points`.
- [ ] Run `python3 -m pytest tests/test_cli_trade_simulation.py tests/test_execution_simulator.py -q`.

### Task 3: Trade Analysis Excursion Metrics

**Files:**
- Modify: `src/full_python/reporting/trade_analysis.py`
- Test: `tests/test_trade_analysis.py`

- [ ] Add a failing test that stopped-trade excursion metrics are calculated from trade CSV fields.
- [ ] Parse optional MFE/MAE columns with default `0.0` for old ledgers.
- [ ] Add `stopped_trade_excursion` to `trade_analysis.json`.
- [ ] Run `python3 -m pytest tests/test_trade_analysis.py tests/test_cli_trade_analysis.py -q`.

### Task 4: Real Smoke And Docs

**Files:**
- Modify: `README.md`
- Create: `docs/runs/2026-07-01-rth-previous-close-excursion-smoke.md`

- [ ] Run the simulator on the selected stream with `--symbol-change-exit-mode previous_close`.
- [ ] Run `analyze-trades` on the new ledger.
- [ ] Document the headline comparison against the previous `next_open` roll behavior.
- [ ] Run `python3 -m pytest -q`.
- [ ] Commit and push the branch.
