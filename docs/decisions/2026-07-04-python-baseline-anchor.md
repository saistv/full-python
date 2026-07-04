# Python Baseline Anchor — Frozen 2026-07-04

**EXECUTED.** The 9-month continuous NQ dataset was assembled from raw
Databento GLBX `ohlcv-1m` files (5-year archive + a `2026-03-16→06-26`
gap-fill batch, both under `NQ 5 years/` in the operator's Dropbox) via
`full_python.data.databento`, validated against two independent checks
before being trusted (bar count = 260,681, exact match to the documented
window; Rule-14 spot check on TV trade #1's fill), then frozen via
`scripts/freeze_baseline_anchor.py` and reconciled against the real TV
AM/DLL export (`AT-RSRCH_CME_MINI_NQ1!_2026-07-03_9e40f.csv`). The numbers
below are real, not placeholders.

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

- Config hash (strategy): `12c1f012a1102f721282f7b00ebd6a927cf3986dae98ff297048555040d0a945`
- Config hash (simulation): `d0db2226fdc5bdb2eb66b8fc46f3ca06c9038acc0a2c2fec806bc7e081d87a79`
- Data hash: `13d3295f4fb07751152e1e999266f171c0636e32fd77e7fb81d7ed9efe246f09`
- Code hash: `d6fc4db7c931f4b5b0651c1dbd588e4f0f12c55e` (git SHA of `claude/m4-regime` at freeze time)
- Full `run_id`: `13d3295f-12c1f012-d0db2226-d6fc4db7`

## Cost model

`point_value=20, commission_rt=10, entry_slippage_points=0.75, exit_slippage_points=0.75, rth_open_extra_entry_slippage_points=0.0` — mirrors the TV reconciliation runs in `docs/decisions/2026-07-03-first-tv-reconciliation.md`, not `SimulationConfig`'s MNQ-first defaults.

**Real-data finding:** `SimulationConfig` defaults `rth_open_extra_entry_slippage_points` to `1.0`, which stacks on top of `entry_slippage_points` during the 9:30-9:45 ET window. Since Adaptive Trend's entry window starts at 9:30, nearly every trade fires inside that window — leaving this at its default silently doubled entry slippage on ~80% of trades and produced a systematic ±1.0-point entry-price offset against every TV-matched trade on the first freeze attempt. Caught by the Parity Delta Report's per-trade decomposition (not the aggregate match rate, which was already 100% at matched_count/tv_trade_count even with the bug present) and fixed in `scripts/freeze_baseline_anchor.py`'s `FROZEN_SIMULATION_OVERRIDES` before this anchor was frozen. See `tests/test_freeze_baseline_anchor.py` for the regression test.

## Strategy

`adaptive_trend_am` (`production_am_config()`) — the production AM (1-4 contract escalation) + equity-based DLL ($1,000) stack, reconciled 106/106 against the TV AM/DLL export per `docs/decisions/2026-07-03-m2b-am-dll-reconciliation.md`. This is the config any promotion path would actually deploy, not the flat 1-contract core.

## Data window

2025-10-01 -> 2026-06-26, 260,681 bars (exact match to the documented window), Databento GLBX continuous front-month (roll = expiry - 3 business days, holiday-aware; observed roll dates: NQZ5→NQH6 2025-12-16, →NQM6 2026-03-17, →NQU6 2026-06-15). Note the sim's trade count over this window is not identical to the 106-trade TV-reconciled count: 9 sim trades before 2025-10-28 are outside TV's 1-minute chart history and are in scope for the Python-only anchor even though they were out of scope for the TV reconciliation.

## Canonical metrics (from `runs/baseline-anchor/report.json`, `metrics` key)

- Trade count: 115 (106 TV-reconciled + 9 out-of-TV-history extras before 2025-10-28)
- Net P&L: $55,875.00
- Win rate: 23.48% (27 wins / 88 losses / 0 scratches)
- Avg win: $4,118.70 / Avg loss: $628.75
- Expectancy per trade: $485.87
- Avg / median R-multiple: 0.673 / -1.039
- Max win / loss streak: 4 / 14
- Max drawdown: -$9,150.00; P&L without best trade: $42,495.00
- Long P&L: $24,305.00; Short P&L: $31,570.00
- By-exit-reason:
  - `atf_flip`: 14 trades, $53,020.00 net, 100% win rate, avg R 5.375
  - `session_flatten`: 13 trades, $58,185.00 net, 100% win rate, avg R 7.191
  - `stop`: 87 trades, -$55,145.00 net, 0% win rate, avg R -1.043
  - `session_end`: 1 trade, -$185.00 net, 0% win rate, avg R -0.578
- Daily: 191 trading days, 113 with trades, 27 profitable (14.1%), best day $13,380.00 (24.0% of net), worst day -$990.00, annualized Sharpe 2.428, max time underwater 55 days

Cross-check: this closely matches the independently-computed figure in `docs/decisions/2026-07-03-m2b-am-dll-reconciliation.md` ("net ≈ $55.9K... max DD ≈ $9.2K"), computed via a different harness on the same window before this anchor/metrics tooling existed — strong evidence the two measurement paths agree.

## Parity validation (performed before trusting this anchor)

1. **Bar count**: 260,681 — exact match to `docs/decisions/2026-07-03-first-tv-reconciliation.md`'s documented window.
2. **Rule-14 spot check**: the assembled series' `2025-10-28T13:32:00Z` bar has `open=26083.75`; `26083.75 + 3 ticks (0.75) = 26084.50`, exactly TV trade #1's documented fill.
3. **Full TV reconciliation** (`AT-RSRCH_CME_MINI_NQ1!_2026-07-03_9e40f.csv`, the `am=1-4|dll=$1000` export, trimmed to entries before 2026-06-27 to match this window): **106/106 matched, 0 missing, 0 quantity mismatches, $0.00 max/mean absolute entry price delta across every trade** — better than the historical doc's one nonzero delta (a June roll-basis artifact), because the TV-exact roll rule already in this codebase (commit `62cbd20`) resolved it. 8 of 106 trades have a small nonzero exit-price delta (max $8.00, on TV#13 — the documented half-day-close case; the rest ≤$1.00, the documented flatten-fill-timing case) — no new, unexplained mismatches. Full detail in `docs/decisions/2026-07-04-parity-delta-report.md`.

## Canonical artifacts

`runs/baseline-anchor/report.json`, `trades.csv`, `events.jsonl`, `daily_pnl.csv`, `report.html`, `reconciliation.json` (gitignored — reproducible from the identity block above plus the operator's copy of the 9-month CSV via `scripts/freeze_baseline_anchor.py`). `tests/fixtures/golden_trades.json` is the one derived artifact that IS committed (via `scripts/export_golden_trades.py`) — it's what makes `tests/test_golden_trades.py`'s regression checks run instead of skip for anyone who checks out this branch.
