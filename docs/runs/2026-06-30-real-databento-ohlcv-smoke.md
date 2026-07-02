# Real Databento OHLCV Smoke

Date: 2026-06-30

Branch: `codex/real-data-baseline-report`

## Purpose

Prove the baseline replay can load one real Databento OHLCV 1-minute `.csv.zst` file and write a reproducible report plus event ledger.

## Input

File:

```text
/Users/sais/Library/CloudStorage/Dropbox/Downloads/Claude_Projects/Hybrid/NQ 5 years/glbx-mdp3-20250203.ohlcv-1m.csv.zst
```

Initial symbol scan:

```text
NQH5: 1380 rows
NQM5: 492 rows
NQU5: 16 rows
NQH5-NQM5: 171 rows
NQM5-NQU5: 6 rows
NQH5-NQU5: 2 rows
```

## Finding

The first Databento loader accepted every row whose symbol started with `NQ`, excluding spreads. That still mixed multiple outright contract months into one replay stream. The report labeled the run as the first symbol, `NQH5`, while the loaded bars also included `NQM5` and `NQU5`.

That is not acceptable for research. A replay must use one explicit contract, or a later roll-adjusted continuous contract with a documented roll method.

## Fix

Databento OHLCV loading now supports exact contract selection with `contract_symbol` / `--contract-symbol`.

If a file has multiple matching outright contracts and no exact contract is provided, the run fails loudly and lists the available symbols.

## Verified Command

```bash
PYTHONPATH=src python3 -m full_python.cli \
  --source-format databento-ohlcv \
  --contract-symbol NQH5 \
  --data "/Users/sais/Library/CloudStorage/Dropbox/Downloads/Claude_Projects/Hybrid/NQ 5 years/glbx-mdp3-20250203.ohlcv-1m.csv.zst" \
  --output-dir /private/tmp/full_python_real_ohlcv_nqh5_20260630
```

## Verified Output

```text
source: databento-ohlcv
symbol: NQ
contract: NQH5
row_count: 1380
start: 2025-02-03T00:00:00Z
end: 2025-02-03T23:59:00Z
events: 4140
symbol-bearing ledger events: NQH5 only
```

The placeholder baseline strategy produced zero trades on this one-day smoke. That is expected at this stage because this milestone validates the data path and ledger, not strategy edge.

## Next Research Implication

The next data milestone should not rely on implicit `NQ*` matching. It should introduce an explicit contract calendar or documented continuous-contract builder before multi-month optimization begins.
