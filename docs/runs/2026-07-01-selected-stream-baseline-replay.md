# Selected Stream Baseline Replay

Date: 2026-07-01

Branch: `codex/real-data-baseline-report`

## Purpose

Run the placeholder baseline replay against the full selected-contract CSV stream. This validates that the replay engine can process the multi-year selected stream without holding the full event ledger in memory.

This is an infrastructure smoke test, not evidence that the placeholder strategy has edge.

## Input

```text
/private/tmp/full_python_selected_stream_20260701/selected_bars.csv
```

## Command

```bash
PYTHONPATH=src python3 -m full_python.cli \
  --data /private/tmp/full_python_selected_stream_20260701/selected_bars.csv \
  --output-dir /private/tmp/full_python_selected_baseline_20260701 \
  --stream-events
```

## Output

```text
/private/tmp/full_python_selected_baseline_20260701/report.json
/private/tmp/full_python_selected_baseline_20260701/events.jsonl
```

## Summary

```text
data_source: csv
contract: MULTI
row_count: 1769265
start_timestamp_utc: 2021-03-16T00:00:00Z
end_timestamp_utc: 2026-03-15T23:59:00Z
event_count: 5307795
events_size: 1.1 GB
strategy: baseline_momentum_breakout
survivability_trade_count: 0
```

Event type counts:

```text
bar: 1769265
signal_decision: 1769265
rejection: 1450471
order_intent: 318794
```

## Finding

The streaming event path is required for full selected-stream replay. The in-memory ledger would need to hold more than five million events for this run.

The baseline report still has placeholder survivability data. The next milestone should convert order intents and future fills/exits into a real trade ledger, then add RTH/session breakdowns before strategy conclusions are drawn.
