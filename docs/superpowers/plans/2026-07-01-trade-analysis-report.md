# Trade Analysis Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an analytics report for generated `trades.csv` files covering period breakdowns, drawdown, loss streak, and top-trade dependency.

**Architecture:** Add `full_python.reporting.trade_analysis` to parse trade CSV rows and calculate reusable metrics. Add an `analyze-trades` CLI command that writes `trade_analysis.json`. Keep this independent from strategy simulation so any future strategy ledger can use it.

**Tech Stack:** Python 3.9, csv/json/dataclasses, pytest.

---

### Task 1: Trade Analysis Module

**Files:**
- Create: `src/full_python/reporting/trade_analysis.py`
- Test: `tests/test_trade_analysis.py`

- [ ] Add tests for max drawdown, max loss streak, monthly/quarterly breakdowns, and P&L without top N trades.
- [ ] Implement CSV parsing and `build_trade_analysis`.
- [ ] Verify focused tests.

### Task 2: CLI Command

**Files:**
- Modify: `src/full_python/cli.py`
- Test: `tests/test_cli_trade_analysis.py`

- [ ] Add `analyze-trades --trades --output-dir`.
- [ ] Write `trade_analysis.json`.
- [ ] Verify focused tests.

### Task 3: Real Smoke And Docs

**Files:**
- Modify: `README.md`
- Create: `docs/runs/2026-07-01-rth-costed-trade-analysis-smoke.md`

- [ ] Run against `/private/tmp/full_python_rth_costed_trade_ledger_20260701/trades.csv`.
- [ ] Document headline analytics.
- [ ] Run full tests, commit, push.
