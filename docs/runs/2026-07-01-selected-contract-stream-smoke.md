# Selected Contract Stream Smoke

Date: 2026-07-01

Branch: `codex/real-data-baseline-report`

## Purpose

Create the first replay-ready Databento NQ bar stream from the dominant contract calendar. This stream loads one selected outright contract per daily file and preserves provenance columns so later research can audit every bar back to its source file and selection rule.

## Command

```bash
PYTHONPATH=src python3 -m full_python.cli build-selected-stream \
  --folder "/Users/sais/Library/CloudStorage/Dropbox/Downloads/Claude_Projects/Hybrid/NQ 5 years" \
  --output-dir /private/tmp/full_python_selected_stream_20260701
```

## Output

```text
/private/tmp/full_python_selected_stream_20260701/selected_bars.csv
/private/tmp/full_python_selected_stream_20260701/selected_bars_manifest.json
```

## Summary

```text
row_count: 1769265
csv lines including header: 1769266
start_timestamp_utc: 2021-03-16T00:00:00Z
end_timestamp_utc: 2026-03-15T23:59:00Z
calendar_entry_count: 1557
source_file_count: 1557
selected_contract_count: 20
skipped_entries: 0
csv size: 391 MB
manifest size: 1.3 MB
```

First sample rows:

```text
2021-03-16T00:00:00Z NQM1 13056.25 glbx-mdp3-20210316.ohlcv-1m.csv.zst 2021-03-16
2021-03-16T00:01:00Z NQM1 13052.25 glbx-mdp3-20210316.ohlcv-1m.csv.zst 2021-03-16
2021-03-16T00:02:00Z NQM1 13051.0 glbx-mdp3-20210316.ohlcv-1m.csv.zst 2021-03-16
```

Selected contracts:

```text
NQH2, NQH3, NQH4, NQH5, NQH6,
NQM1, NQM2, NQM3, NQM4, NQM5,
NQM6, NQU1, NQU2, NQU3, NQU4,
NQU5, NQZ1, NQZ2, NQZ3, NQZ4
```

## Caveat

This stream is replay-ready, but its roll behavior is only as good as the current first-pass selection rule: `dominant_outright_row_count`. It is suitable for building the replay pipeline and baseline analytics. It should not yet be treated as a final production continuous contract methodology.

## Next Step

Run the baseline replay against `selected_bars.csv`, then compare results by year, quarter, selected contract, and RTH-only window before porting real strategy logic.
