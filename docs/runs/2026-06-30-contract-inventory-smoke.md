# Contract Inventory Smoke

Date: 2026-06-30

Branch: `codex/real-data-baseline-report`

## Purpose

Scan the real Databento NQ OHLCV folder before building a contract calendar or running multi-month optimization. This protects the research process from silently mixing contract months or spread symbols into one replay stream.

## Command

```bash
PYTHONPATH=src python3 -m full_python.cli inventory-databento \
  --folder "/Users/sais/Library/CloudStorage/Dropbox/Downloads/Claude_Projects/Hybrid/NQ 5 years" \
  --output-dir /private/tmp/full_python_contract_inventory_20260630 \
  --markdown
```

## Output

```text
/private/tmp/full_python_contract_inventory_20260630/contract_inventory.json
/private/tmp/full_python_contract_inventory_20260630/contract_inventory.md
```

## Summary

```text
files scanned: 1557
markdown rows: 7017
json lines: 44404
```

Most common symbols by total row count:

```text
NQZ4: 122487
NQM2: 120185
NQM4: 119549
NQZ3: 119538
NQM5: 118503
NQU4: 116889
NQH2: 116636
NQU2: 116195
NQU5: 115917
NQZ1: 112885
NQH4: 112791
NQZ2: 112560
```

First scanned file:

```text
glbx-mdp3-20210316.ohlcv-1m.csv.zst
symbols: NQH1, NQH1-NQM1, NQH1-NQU1, NQM1, NQM1-NQU1, NQU1, NQZ1
```

Last scanned file:

```text
glbx-mdp3-20260315.ohlcv-1m.csv.zst
symbols: NQH6, NQH6-NQM6, NQH6-NQU6, NQM6, NQU6
```

## Finding

The real Databento folder is not one clean continuous NQ stream. Many daily files contain several outright contracts plus calendar spreads. The next milestone should build a versioned contract calendar or continuous-contract builder from this inventory before any multi-month optimization is trusted.
