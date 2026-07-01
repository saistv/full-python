# Dominant Contract Calendar Smoke

Date: 2026-07-01

Branch: `codex/real-data-baseline-report`

## Purpose

Build the first auditable daily contract-selection calendar from the real Databento NQ OHLCV folder. This is the bridge between raw multi-symbol daily files and future multi-month replay inputs.

## Command

```bash
PYTHONPATH=src python3 -m full_python.cli build-contract-calendar \
  --folder "/Users/sais/Library/CloudStorage/Dropbox/Downloads/Claude_Projects/Hybrid/NQ 5 years" \
  --output-dir /private/tmp/full_python_contract_calendar_20260701 \
  --markdown
```

## Selection Rule

```text
dominant_outright_row_count
```

The rule ignores spread symbols containing `-`, then selects the outright contract with the highest row count in each daily file. Ties are resolved by symbol name so output is deterministic.

This is not final roll research and not a back-adjusted continuous contract. It is a transparent first-pass rule that makes every replay contract choice explicit.

## Output

```text
/private/tmp/full_python_contract_calendar_20260701/contract_calendar.json
/private/tmp/full_python_contract_calendar_20260701/contract_calendar.md
```

## Summary

```text
entries: 1557
json lines: 38954
markdown lines: 1565
first entry: 2021-03-16 -> NQM1
last entry: 2026-03-15 -> NQH6
```

Most common selected contracts by daily file count:

```text
NQM4: 83
NQH2: 80
NQU1: 79
NQU2: 79
NQZ4: 79
NQU5: 79
NQH6: 79
NQU3: 78
NQZ3: 78
NQU4: 78
NQH5: 78
NQZ1: 77
```

First Markdown rows:

```text
2021-03-16 -> NQM1
2021-03-17 -> NQM1
2021-03-18 -> NQM1
2021-03-19 -> NQM1
2021-03-21 -> NQM1
```

## Finding

The dominant-row-count rule creates a complete daily selection calendar for the available folder. The next milestone should use this calendar to load one selected contract per file and produce a clean replay-ready continuous bar stream, while preserving source contract, file path, and selection rule metadata on every bar or manifest row.
