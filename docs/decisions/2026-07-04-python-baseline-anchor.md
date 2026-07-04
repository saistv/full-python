# Python Baseline Anchor — Frozen 2026-07-04

> **NOT YET EXECUTED.** Placeholders below (`<paste ...>`) require running
> `scripts/freeze_baseline_anchor.py` against the real
> 2025-10-01 -> 2026-06-26 dataset via `FULL_PYTHON_BASELINE_DATA`; this has
> not yet been executed in this environment. The 9-month CSV does not exist
> in this sandboxed worktree. Do not treat any placeholder as a real number,
> and do not fill one in without actually running the script against the
> real data first.

Replaces the missing 3-year TradingView export as the reference point for
all future Python-side comparisons (see the Python Reference Engine
Migration plan). This anchor is immutable once committed: a future change
is judged against these exact numbers, not re-frozen to make a comparison
look favorable.

## Explicit scope-down (read this first)

The original migration spec called for a 3-year canonical window. **No
3-year NQ continuous dataset exists in this repository or on this
branch.** The only reconciled-against-TradingView window available is
2025-10-01 -> 2026-06-26 (9 months, 260,681 bars). This anchor freezes on
that 9-month window, not 3 years. Any Gate 1 promotion-table row that
needs 3 years of history (year-by-year robustness, a 2023-01 train split)
is **not supportable by this anchor** and must be flagged as data-limited
wherever it is invoked (see Task 8's sizing-gate doc).

## Exact identity

- Config hash (strategy): `<paste strategy.parameter_hash from report.json>`
- Config hash (simulation): `<paste simulation.parameter_hash from report.json>`
- Data hash: `<paste data.manifest_hash from report.json>`
- Code hash: `<paste code_version from report.json>` (git SHA of `claude/m4-regime` at freeze time)
- Full `run_id`: `<paste run_id from report.json>`

## Cost model

`point_value=20, commission_rt=10, entry_slippage_points=0.75, exit_slippage_points=0.75` — mirrors the TV reconciliation runs in `docs/decisions/2026-07-03-first-tv-reconciliation.md`, not `SimulationConfig`'s MNQ-first defaults.

## Strategy

`adaptive_trend_am` (`production_am_config()`) — the production AM (1-4 contract escalation) + equity-based DLL ($1,000) stack, reconciled 106/106 against the TV AM/DLL export per `docs/decisions/2026-07-03-m2b-am-dll-reconciliation.md`. This is the config any promotion path would actually deploy, not the flat 1-contract core.

## Data window

2025-10-01 -> 2026-06-26, 260,681 bars, Databento GLBX continuous front-month (roll = expiry - 3 business days, holiday-aware). Note the sim's trade count over this window is not identical to the 120-trade TV-reconciled count: 9 sim trades before 2025-10-28 are outside TV's 1-minute chart history and are in scope for the Python-only anchor even though they were out of scope for the TV reconciliation.

## Canonical metrics (from `runs/baseline-anchor/report.json`, `metrics` key)

- Trade count: `<paste metrics.expectancy.trade_count>`
- Net P&L: `<paste survivability.net_pnl>`
- Win rate: `<paste metrics.expectancy.win_rate>`
- Expectancy per trade: `<paste metrics.expectancy.expectancy_dollars>`
- Avg / median R-multiple: `<paste metrics.expectancy.avg_r_multiple>` / `<paste metrics.expectancy.median_r_multiple>`
- Max win / loss streak: `<paste metrics.max_win_streak>` / `<paste metrics.max_loss_streak>`
- By-exit-reason: `<paste metrics.by_exit_reason>`

## Canonical artifacts

`runs/baseline-anchor/report.json`, `trades.csv`, `events.jsonl`, `daily_pnl.csv`, `report.html` (gitignored — reproducible from the identity block above plus the operator's copy of the 9-month CSV via `scripts/freeze_baseline_anchor.py`).
