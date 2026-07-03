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
- `full_python.events`: append-only event records, stable event IDs, JSONL persistence.
- `full_python.models`: immutable domain records for market bars, signal decisions, order intents, risk vetoes, stop updates, exits, fills, and closed trades.
- `full_python.strategy`: baseline momentum-breakout strategy (entry, breakdown exit signal, frozen stop) and hashed config.
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
