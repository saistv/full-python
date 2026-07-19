# Full Python

Python-first NQ/MNQ research, replay, shadow-trading, and eventual execution system.

This project is intentionally separate from the Pine/TradingView research repo. Pine is now treated as legacy reference material. Full Python is where the canonical strategy engine, deterministic replay, event ledger, risk manager, and execution adapters will live.

## First Milestone

Build the canonical replay and event-ledger foundation:

- Load real market data.
- Run deterministic strategy decisions.
- Log every signal, rejection, order intent, stop update, exit, risk veto, and state transition.
- Keep execution disabled until replay and shadow mode are trustworthy.

## Current Components

- `full_python.data`: CSV market-bar loading, data manifests with content checksums and stable provenance hashes, the ET/CME session model (RTH classification, 18:00 ET session boundary), and structural data validation (ordering, duplicates, malformed OHLC, gap accounting).
- `full_python.indicators`: streaming Pine-semantics primitives (EMA, SMA, population stdev, RMA/ATR, true range, rolling extrema, linreg endpoint, strict pivots with Pine's shift-and-fixnan view) plus the ATF trend state machine and squeeze momentum composites. Streaming by design: replay and future live shadow share the same indicator code path.
- `full_python.events`: append-only event records, stable event IDs, JSONL persistence.
- `full_python.models`: immutable domain records for market bars, signal decisions, order intents, risk vetoes, stop updates, exits, fills, and closed trades.
- `full_python.strategy`: baseline momentum-breakout strategy and the frozen Adaptive Trend historical candidate (pivot S/R breakout + prove-it, squeeze momentum, wings candle gate, MA50/MA200, ATF alignment, 9:30-10:00 ET window, cooldowns, Dynamic S/R stop 5/15/31, anti-martingale sizing, and daily-loss controls). Its historical arithmetic is reproducible; this is not an independently validated edge or permission to trade.
- `full_python.reconcile`: trade-by-trade reconciliation against TradingView "List of trades" exports — matched/missing/extra with entry-time and price deltas. This is the authority gate: aggregate agreement is not accepted as evidence.
- `full_python.replay`: deterministic replay loop that feeds bars to a strategy and records resulting events in a fixed order.
- `full_python.simulation`: deterministic fill/position engine — next-bar-open fills with adverse slippage, frozen stops with gap-through handling, worst-case intrabar ordering with ambiguity flagging, session risk gate (RTH-only entries, 15:59 ET backstop, session-boundary flatten), and costs always applied. Policy: `docs/decisions/2026-07-03-fill-simulation-policy.md`.
- `full_python.reporting`: survivability metrics plus daily-resolution metrics (annualized Sharpe over the full trading calendar including flat days, time underwater, profitable-day rate, best-day dependency) and monthly breakdowns.
- `full_python.cli`: baseline run command that writes `events.jsonl`, `trades.csv`, `daily_pnl.csv`, and `report.json` with a deterministic run ID.

## Migration Rule

Port concepts, not clutter:

- Port now: ATF, squeeze, S/R breakout, prove-it, wings, MFE/MAE tracking, deterministic replay.
- Research separately: regime permissions, mean reversion, dynamic windows, and sizing changes, all under predeclared gates.
- Do not port by default: dead AER, stale sizing assumptions, breakeven stop clutter, or unvalidated scratch exits.

## Baseline Replay Command

The first baseline command expects a CSV with:

```text
timestamp,symbol,open,high,low,close,volume
```

Run:

```bash
PYTHONPATH=src python3 -m full_python.cli --data path/to/bars.csv --output-dir runs/baseline-smoke
```

The command writes:

- `events.jsonl` — the full event ledger (bars, signals, rejections, vetoes, fills, trades)
- `trades.csv` — closed trades with fills, costs, MFE/MAE, and ambiguity flags
- `daily_pnl.csv` — per-session P&L and cumulative equity
- `report.json` — manifest, data quality, config hashes, survivability, daily and monthly metrics

Useful flags:

- `--fill-timing signal_bar_close` — legacy TradingView reconciliation mode only; never for promotion decisions
- `--allow-dirty-data` — proceed despite structural data issues (they are still reported)

Two runs over the same data and configs produce byte-identical event logs and the same run ID.

## Run Reports & Perturbation

Every CLI run writes a self-contained `report.html` next to `report.json`:
equity curve with drawdown shading, trade P&L distribution, winners/losers
stats, monthly breakdown, exit reasons, entry sizing, and rejected signals
by gate — one file, no external assets, opens anywhere.

Single-axis sensitivity sweeps (measurement, not optimization — single-axis
sweeps cannot see parameter interactions, and production changes still
require the full promotion gate):

```bash
PYTHONPATH=src python3 -m full_python.perturb --data bars.csv --strategy adaptive_trend_am \
  --vary prove_it_bars=1,2,3 --vary wings_close_frac=0.55,0.6,0.65,0.7,0.75 --output sweep.json
```

## Adaptive Trend Run + TradingView Reconciliation

```bash
PYTHONPATH=src python3 -m full_python.cli --data path/to/nq_1m.csv --output-dir runs/at-flat --strategy adaptive_trend
PYTHONPATH=src python3 -m full_python.reconcile --tv path/to/tv_trade_list.csv --trades runs/at-flat/trades.csv --output runs/at-flat/reconciliation.json
```

Reconciliation protocol (the authority gate):

1. Run the Pine research fork in TradingView at flat 1-contract sizing (anti-martingale off, DLL off) and export the trade list.
2. Feed the same session coverage to the CLI that the TV chart used (warmup counts bars, so ETH-inclusive charts need ETH-inclusive data).
3. Use `--fill-timing signal_bar_close` only if the TV run filled on signal-bar close; the default matches `process_orders_on_close=false`.
4. Every missing/extra trade must be explained (fill timing, intrabar ambiguity, roll boundary, data gap) or fixed before the Python engine is treated as authoritative. Aggregate P&L agreement alone is not evidence — the legacy Python backtester agreed in aggregate while being +23% wrong.

## Opening Auction Regime v1 Research

The opening-auction candidate is a separate preregistered strategy, not a
revision of Adaptive Trend and not a revival of the rejected generic VWAP/OR
fades. Its first authoritative command is intentionally train-only:

```bash
PYTHONPATH=src:. python3 scripts/run_opening_auction_experiment.py \
  --data runs/multi-year/nq1_2021-03-16_2026-06-26.csv \
  --output-dir runs/opening-auction-regime-v1/train-t1 \
  --registry runs/opening-auction-regime-v1/experiments.sqlite
```

The runner physically stops before the CME session dated 2025-01-01, registers
T1 before the first strategy decision, freezes NQ costs/fill timing, and writes:

- `report.json` — gates, branch/side/calendar results, feature distributions,
  fill-relative R, cost drag, and the explicit proceed/reject decision;
- `events.jsonl` and `trades.csv` — canonical execution evidence;
- `auction_sessions.csv` — every frozen classification, including no-trade;
- `auction_diagnostics.csv` — arm/cancel/confirm/fill/exit funnel events;
- `experiments.sqlite` — the insert-only 11-trial budget.

Do not use the normal all-history CLI to make a research claim for this
candidate. T2-T11 are forbidden unless frozen T1 passes its primary gates, and
the historical result can never replace the required prospective 126-session
shadow window. Full contract:
`docs/research/2026-07-17-opening-auction-regime-v1-hypothesis.md`.

## Opening Auction Level-Retest v2

V1 was rejected on its frozen train trial. V2 is a new external-level
acceptance/rejection hypothesis, not a threshold rescue: the 09:44 auction state
is context only, and an entry requires the first later retest to hold plus a
separate confirmation bar. Run the one registered train trial with:

```bash
PYTHONPATH=src:. python3 scripts/run_opening_auction_retest_experiment.py \
  --data runs/multi-year/nq1_2021-03-16_2026-06-26.csv \
  --output-dir runs/opening-auction-retest-v2/train-t1 \
  --registry runs/opening-auction-retest-v2/experiments.sqlite
```

The runner refuses to overwrite either artifact location, validates monotonic
timestamps, never constructs a bar at or after the 2025-01-01 session boundary,
and hashes the code, data, hypothesis, configuration, simulation, and permanent
promotion standard, plus the evaluation policy, emitted artifacts, canonical
replay cores, and research result. Optional `--allocated-capital` and
`--hard-loss-limit` values must be supplied together as finite positive dollar
amounts. They evaluate the p99 drawdown budget; omitting them permits the T1
primary decision but blocks permanent capital promotion without changing strategy
behavior.

The frozen rules are in
`docs/research/2026-07-17-opening-auction-retest-v2-hypothesis.md`. The permanent
pass/fail contract for every strategy is
`docs/specs/2026-07-17-automation-worthiness-standard.md`. A failed T1 closes v2;
no side, branch, threshold, or date slice may be changed to rescue it.

T1 was executed once and rejected: 11 trades, -$1,655 net, PF 0.636, daily
Sharpe -0.289, and 71.45% block-bootstrap probability of nonpositive total P&L.
The registry is closed; do not rerun T1 or run T2-T9. The immutable decision is in
`docs/research/2026-07-17-opening-auction-retest-v2-verdict.md`.

## Overnight Displacement Reversal v3

V3 is an independent, mirrored overnight-displacement hypothesis. It does not
reuse Adaptive Trend momentum, v1's opening-auction classifier, or v2's external
level retest. The one-minute OHLCV signal measures a price-based displacement
proxy, not actual dealer or trader inventory. Its only historical composition
root is the sealed runner:

```bash
PYTHONPATH=src:. python3 scripts/run_overnight_displacement_reversal_experiment.py \
  --data runs/multi-year/nq1_2021-03-16_2026-06-26.csv \
  --output-dir runs/overnight-displacement-reversal-v3/train-t1 \
  --registry runs/overnight-displacement-reversal-v3/experiments.sqlite
```

The runner is deliberately absent from the general CLI. It accepts no strategy
threshold overrides, refuses to overwrite either artifact location, requires
canonical minute-aligned UTC timestamps, and constructs no market bar at or after
the CME session beginning 2025-01-01. T1 is registered before the first strategy
bar and then executed twice with fresh objects. Ledger, trades, session sequence,
session snapshots, diagnostics, and the research core must hash identically.

It writes:

- `events.jsonl` and `trades.csv` — canonical execution evidence;
- `displacement_sessions.csv` — every frozen displacement classification, including
  fail-closed and no-trade sessions;
- `displacement_diagnostics.csv` — extension, rejection, cancellation, bracket,
  fill, and exit funnel events;
- `report.json` — the complete scorecard, provenance, replay hashes, and frozen
  proceed/reject decision; and
- `experiments.sqlite` — the insert-only nine-trial registry.

Optional `--allocated-capital` and `--hard-loss-limit` values must be supplied
together as finite positive dollar amounts. They report the p99 drawdown capital
policy but do not change strategy behavior or rescue a failed T1. T2-T9 remain
forbidden unless every normal-cost T1 primary gate passes. The complete frozen
contract is
`docs/research/2026-07-18-overnight-displacement-reversal-v3-hypothesis.md`.

T1 was executed once and rejected: 155 trades, +$3,865 net, PF 1.060,
daily Sharpe 0.165, -0.0165R per calendar week, and 37.885% block-bootstrap
probability of nonpositive total P&L. The small positive dollar result was
concentrated in a few trades and the long book lost money. The registry is closed;
do not rerun T1, run T2-T9, tune v3, or port it to Pine. The immutable result and
the post-run explanation of seven evaluator false positives are in
`docs/research/2026-07-18-overnight-displacement-reversal-v3-verdict.md`.
