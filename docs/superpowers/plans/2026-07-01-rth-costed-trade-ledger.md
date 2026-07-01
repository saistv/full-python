# RTH Costed Trade Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add full-RTH filtering and explicit slippage/commission/point-value assumptions to the first trade ledger simulator.

**Architecture:** Add a small `full_python.data.sessions` module for New York RTH filtering. Extend the execution simulator with a `SimulationCosts` dataclass and dollar P&L fields. Extend `simulate-baseline-trades` with `--session`, `--point-value`, `--slippage-points-per-side`, and `--commission-per-contract`.

**Tech Stack:** Python 3.9, standard `zoneinfo`, dataclasses, csv/json, pytest.

---

### Task 1: Session Filtering

**Files:**
- Create: `src/full_python/data/sessions.py`
- Test: `tests/test_sessions.py`

- [ ] Write tests for UTC timestamps inside/outside New York RTH.
- [ ] Implement `is_rth_bar` and `filter_bars_by_session`.
- [ ] Verify with `python3 -m pytest tests/test_sessions.py -q`.

### Task 2: Costed Simulator

**Files:**
- Modify: `src/full_python/execution/simulator.py`
- Modify: `tests/test_execution_simulator.py`

- [ ] Add failing tests for slippage, commission, point value, and net dollar P&L.
- [ ] Implement `SimulationCosts`.
- [ ] Add gross/net dollar fields to `TradeFill`.
- [ ] Include cost settings in summary assumptions.
- [ ] Verify with `python3 -m pytest tests/test_execution_simulator.py -q`.

### Task 3: CLI And Real Smoke

**Files:**
- Modify: `src/full_python/cli.py`
- Modify: `tests/test_cli_trade_simulation.py`
- Modify: `README.md`
- Create: `docs/runs/2026-07-01-rth-costed-trade-ledger-smoke.md`

- [ ] Add `simulate-baseline-trades --session rth --point-value 2 --slippage-points-per-side 1 --commission-per-contract 1`.
- [ ] Verify CLI test.
- [ ] Run real selected-stream smoke.
- [ ] Document results.
- [ ] Run full tests, commit, push.
