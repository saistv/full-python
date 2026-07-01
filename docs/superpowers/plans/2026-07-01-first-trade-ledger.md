# First Trade Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first explicit trade/fill ledger so baseline strategy order intents become auditable trades with clear fill and stop assumptions.

**Architecture:** Add a focused `full_python.execution.simulator` module that runs a strategy over bars, fills one long position at a time at bar close, exits on stop touch, and forces end-of-data exit. Add CSV/JSON writers and a CLI command for selected-stream research. Keep this separate from `ReplayEngine` so event logging and trade simulation can evolve independently.

**Tech Stack:** Python 3.9, dataclasses, csv/json, existing bar loaders and baseline strategy, pytest.

---

### Task 1: Execution Simulator Module

**Files:**
- Create: `src/full_python/execution/simulator.py`
- Test: `tests/test_execution_simulator.py`

- [ ] **Step 1: Write failing tests**

Test that a breakout order enters long at close, exits at stop when low touches stop, ignores additional entries while in position, and force-exits at end of data.

- [ ] **Step 2: Run focused tests to verify failure**

Run:

```bash
python3 -m pytest tests/test_execution_simulator.py -q
```

Expected: fail because `full_python.execution.simulator` does not exist.

- [ ] **Step 3: Implement module**

Create `TradeFill`, `TradeLedger`, and `simulate_strategy_trades`. Assumptions:

- one open position max
- long entries only for now
- fill entry at current bar close when order intent side is `buy`
- read stop from `order_intent.metadata["stop_price"]`
- on later bars, if `bar.low <= stop_price`, exit at stop price
- on final bar, force exit at close if still open
- `pnl_points = exit_price - entry_price`

- [ ] **Step 4: Run focused tests**

Run:

```bash
python3 -m pytest tests/test_execution_simulator.py -q
```

Expected: pass.

### Task 2: Writers And CLI

**Files:**
- Modify: `src/full_python/cli.py`
- Test: `tests/test_cli_trade_simulation.py`

- [ ] **Step 1: Write failing CLI test**

Test:

```bash
python3 -m full_python.cli simulate-baseline-trades --data bars.csv --output-dir run
```

Expected files:

```text
trades.csv
trade_summary.json
```

- [ ] **Step 2: Implement command**

Command args:

```text
simulate-baseline-trades --data --output-dir --stream-input
```

Use the simple CSV column map, baseline strategy, and execution simulator.

- [ ] **Step 3: Run CLI test**

Run:

```bash
python3 -m pytest tests/test_cli_trade_simulation.py -q
```

Expected: pass.

### Task 3: Real Smoke And Docs

**Files:**
- Modify: `README.md`
- Create: `docs/runs/2026-07-01-first-trade-ledger-smoke.md`

- [ ] **Step 1: Run real smoke**

Run against the selected stream:

```bash
PYTHONPATH=src python3 -m full_python.cli simulate-baseline-trades \
  --data /private/tmp/full_python_selected_stream_20260701/selected_bars.csv \
  --output-dir /private/tmp/full_python_trade_ledger_20260701 \
  --stream-input
```

- [ ] **Step 2: Document output**

Record trade count, total points, win rate, first/last timestamps, and assumptions.

- [ ] **Step 3: Verify, commit, push**

Run:

```bash
python3 -m pytest -q
git add README.md docs/runs/2026-07-01-first-trade-ledger-smoke.md docs/superpowers/plans/2026-07-01-first-trade-ledger.md src/full_python/cli.py src/full_python/execution/simulator.py tests/test_cli_trade_simulation.py tests/test_execution_simulator.py
git commit -m "feat: add first trade ledger simulator"
git push
```
