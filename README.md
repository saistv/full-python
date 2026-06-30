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

- `full_python.events`: append-only event records, stable event IDs, JSONL persistence.
- `full_python.models`: immutable domain records for market bars, signal decisions, order intents, risk vetoes, stop updates, and exits.
- `full_python.replay`: deterministic replay loop that feeds bars to a strategy and records resulting events in a fixed order.

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
python3 -m full_python.cli --data path/to/bars.csv --output-dir runs/baseline-smoke
```

The command writes:

- `events.jsonl`
- `report.json`
