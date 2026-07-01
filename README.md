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

- `full_python.data`: CSV market-bar loading plus data manifests with content checksums, row counts, file sizes, column maps, and stable provenance hashes.
- `full_python.events`: append-only event records, stable event IDs, JSONL persistence.
- `full_python.models`: immutable domain records for market bars, signal decisions, order intents, risk vetoes, stop updates, and exits.
- `full_python.strategy`: baseline momentum configuration and placeholder strategy surface for deterministic replay wiring.
- `full_python.replay`: deterministic replay loop that feeds bars to a strategy and records resulting events in a fixed order.
- `full_python.reporting`: survivability and trade-analysis metrics for baseline reports.
- `full_python.cli`: baseline replay command that writes `events.jsonl` and `report.json`.

## Migration Rule

Port concepts, not clutter:

- Port now: ATF, squeeze, S/R breakout, prove-it, wings, MFE/MAE tracking, deterministic replay.
- Research later: regime classifier, mean reversion, dynamic windows, MNQ sizing.
- Do not port by default: dead AER, old anti-martingale assumptions, breakeven stop clutter, unvalidated scratch exits.

## Baseline Replay Command

The baseline command supports a simple CSV with:

```text
timestamp,symbol,open,high,low,close,volume
```

Run the simple CSV path:

```bash
PYTHONPATH=src python3 -m full_python.cli --data path/to/bars.csv --output-dir runs/baseline-smoke
```

For large CSV inputs, stream events directly to JSONL instead of storing the full event ledger in memory:

```bash
PYTHONPATH=src python3 -m full_python.cli --data path/to/selected_bars.csv --output-dir runs/baseline-selected --stream-events
```

Run one Databento OHLCV 1-minute `.csv.zst` file:

```bash
PYTHONPATH=src python3 -m full_python.cli --source-format databento-ohlcv --contract-symbol NQH5 --data path/to/NQ.ohlcv-1m.csv.zst --output-dir runs/databento-baseline
```

Databento files can contain multiple outright contracts for the same product. If more than one matching contract is present, pass `--contract-symbol` so a replay cannot silently mix contract months. Databento loading keeps rows whose `symbol` starts with `NQ` by default and excludes spread symbols containing `-`. Use `--symbol-root` to choose another root and `--include-spreads` to inspect spreads.

The command writes:

- `events.jsonl`
- `report.json`

## Databento Contract Inventory

Before replaying or optimizing multi-month Databento data, inventory the raw folder:

```bash
PYTHONPATH=src python3 -m full_python.cli inventory-databento --folder path/to/NQ-data --output-dir runs/contract-inventory --markdown
```

This writes:

- `contract_inventory.json`
- `contract_inventory.md`, when `--markdown` is passed

The inventory lists every matching symbol per file, including outright contracts and spread symbols whose `symbol` starts with the selected root. Use it before building replay inputs so contract selection, roll behavior, and spread exclusions are explicit instead of assumed.

## Dominant Contract Calendar

After inventorying the raw folder, build a first-pass contract calendar:

```bash
PYTHONPATH=src python3 -m full_python.cli build-contract-calendar --folder path/to/NQ-data --output-dir runs/contract-calendar --markdown
```

This writes:

- `contract_calendar.json`
- `contract_calendar.md`, when `--markdown` is passed

The first calendar rule is `dominant_outright_row_count`: ignore spread symbols, then choose the outright contract with the most rows in each daily file. This is an auditable starting point for replay input selection, not the final roll methodology or a back-adjusted continuous contract.

## Selected Contract Stream

Build a replay-ready CSV from the dominant contract calendar:

```bash
PYTHONPATH=src python3 -m full_python.cli build-selected-stream --folder path/to/NQ-data --output-dir runs/selected-stream
```

This writes:

- `selected_bars.csv`
- `selected_bars_manifest.json`

The CSV keeps the canonical replay columns first:

```text
timestamp,symbol,open,high,low,close,volume
```

It also preserves provenance columns:

```text
source_file,trading_date,selected_contract,selection_rule
```

Current replay can load this CSV through the simple CSV path while ignoring the provenance columns. Research code should keep the manifest beside the CSV so the roll/selection assumptions stay attached to every run.

## First Trade Ledger

Simulate first-pass baseline trades from a CSV bar stream:

```bash
PYTHONPATH=src python3 -m full_python.cli simulate-baseline-trades --data path/to/selected_bars.csv --output-dir runs/trade-ledger --stream-input --session rth --point-value 2 --slippage-points-per-side 1 --commission-per-contract 1 --symbol-change-exit-mode previous_close
```

This writes:

- `trades.csv`
- `trade_summary.json`

Current assumptions are deliberately simple: one long position at a time, entry at current bar close, stop exit when a later bar low touches the stop, and end-of-data exit at final close. Use `--session rth` for full regular trading hours based on New York time. Cost assumptions are explicit through point value, slippage points per side, and commission per contract per side.

Use `--symbol-change-exit-mode previous_close` for research runs that should avoid importing new-contract roll gaps into open-trade P&L. The legacy-compatible mode is `next_open`, which exits at the new contract bar open. Trade ledgers include `max_favorable_excursion_points` and `max_adverse_excursion_points` for every trade.

For exit-conversion research, enable completed-bar MFE trailing:

```bash
PYTHONPATH=src python3 -m full_python.cli simulate-baseline-trades --data path/to/selected_bars.csv --output-dir runs/trade-ledger-mfe-trail --stream-input --session rth --point-value 2 --slippage-points-per-side 1 --commission-per-contract 1 --symbol-change-exit-mode previous_close --mfe-trailing-activation-points 40 --mfe-trailing-giveback-points 20 --cooldown-bars-after-exit 10
```

The MFE trailing rule activates only after a completed bar has reached the configured favorable excursion. The resulting trailing stop can exit on later bars with `exit_reason=mfe_trailing_stop`; it does not assume same-bar high/low ordering.

Re-entry control blocks same-bar re-entry after every exit. Use `--cooldown-bars-after-exit` to also block the next N bars after an exit. This is research instrumentation for churn control, not a signal-edge claim by itself.

## Trade Analysis Report

Analyze any generated `trades.csv` ledger:

```bash
PYTHONPATH=src python3 -m full_python.cli analyze-trades --trades runs/trade-ledger/trades.csv --output-dir runs/trade-analysis
```

This writes:

- `trade_analysis.json`

The report includes headline P&L, drawdown, max loss streak, top-trade dependency, monthly and quarterly breakdowns, exit-reason breakdowns, symbol breakdowns, side breakdowns, and stopped-trade MFE/MAE metrics. Use this after every candidate simulation so results are judged by survivability and robustness, not just net P&L.
