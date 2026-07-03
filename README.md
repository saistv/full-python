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
- `full_python.strategy`: baseline momentum-breakout strategy (entry, breakdown exit signal, frozen stop) and the Adaptive Trend port — the validated production signal core (pivot S/R breakout + prove-it, squeeze momentum, wings candle gate, MA50/MA200, ATF alignment, 9:30-10:00 ET window, cooldowns, Dynamic S/R stop 5/15/31) at flat 1-contract sizing. Anti-martingale and the daily loss limit are deliberately deferred until flat parity is proven.
- `full_python.reconcile`: trade-by-trade reconciliation against TradingView "List of trades" exports — matched/missing/extra with entry-time and price deltas. This is the authority gate: aggregate agreement is not accepted as evidence.
- `full_python.replay`: deterministic replay loop that feeds bars to a strategy and records resulting events in a fixed order.
- `full_python.simulation`: deterministic fill/position engine — next-bar-open fills with adverse slippage, frozen stops with gap-through handling, worst-case intrabar ordering with ambiguity flagging, session risk gate (RTH-only entries, 15:59 ET backstop, session-boundary flatten), and costs always applied. Policy: `docs/decisions/2026-07-03-fill-simulation-policy.md`.
- `full_python.reporting`: survivability metrics plus daily-resolution metrics (annualized Sharpe over the full trading calendar including flat days, time underwater, profitable-day rate, best-day dependency) and monthly breakdowns.
- `full_python.cli`: baseline run command that writes `events.jsonl`, `trades.csv`, `daily_pnl.csv`, and `report.json` with a deterministic run ID.

## Migration Rule

Port concepts, not clutter:

- Port now: ATF, squeeze, S/R breakout, prove-it, wings, MFE/MAE tracking, deterministic replay.
- Research later: regime classifier, mean reversion, dynamic windows, MNQ sizing.
- Do not port by default: dead AER, old anti-martingale assumptions, breakeven stop clutter, unvalidated scratch exits.

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
