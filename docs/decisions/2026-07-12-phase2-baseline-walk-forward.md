# Phase 2 Baseline Anchored Walk-Forward

**Status:** baseline characterization complete; Phase 2 remains in progress.
No parameter selection occurred.

## Registered Design

- Experiment: `phase2-baseline-walk-forward-v1`
- Trial budget: 2 (`NQ`, `MNQ` execution instruments)
- Data: corrected five-year authority reports
- Initial train: 2021-03-16 through 2022-12-31
- Forward windows: non-overlapping six-month segments from 2023-01-01 through
  2026-06-26
- Hypothesis filed in the registry: the locked edge is positive in most forward
  segments
- Selection: none; this measures the frozen baseline

The SQLite registry stores data, strategy, simulation, and source hashes before
trial rows, enforces a fixed trial budget, and refuses trials after completion.
Artifact: `runs/phase2-experiments.sqlite` (gitignored).

## Results

| Forward segment | NQ net | NQ PF | MNQ net | MNQ PF |
|---|---:|---:|---:|---:|
| 2023 H1 | -$885 | 0.972 | -$252.00 | 0.932 |
| 2023 H2 | -$7,515 | 0.803 | -$1,429.00 | 0.684 |
| 2024 H1 | $575 | 1.019 | $270.50 | 1.076 |
| 2024 H2 | $43,855 | 2.184 | $4,704.50 | 1.989 |
| 2025 H1 | $28,265 | 1.777 | $9,583.00 | 3.016 |
| 2025 H2 | $2,310 | 1.071 | -$126.50 | 0.971 |
| 2026 H1 through Jun 26 | $50,800 | 2.272 | $8,711.50 | 2.493 |

NQ is positive in 5 of 7 forward segments; MNQ is positive in 4 of 7. Both
fail in both halves of 2023, and MNQ is also slightly negative in 2025 H2.
The hypothesis passes only in the literal majority sense. It does not show a
smooth or consistently available edge.

## Interpretation

The five-year aggregate is not fabricated by one isolated trade, but it is
strongly era-dependent. Most forward profit arrives in 2024 H2, 2025 H1, and
2026 H1. The complete 2023 loss regime is long enough that a live operator must
expect the system to look broken for many months while still behaving within
historical precedent.

This strengthens three requirements:

1. Capital planning uses bootstrap p95/p99 drawdown, not observed drawdown.
2. The market-state layer is initially monitoring-only. A gate trained to
   remove 2023 after seeing it would be classic hindsight fitting.
3. Candidate changes must improve multiple separate forward segments, not only
   total net or the recent 2024-2026 regime.

Reproduction:

```bash
PYTHONPATH=src python3 scripts/run_baseline_walk_forward.py \
  --registry runs/phase2-experiments.sqlite \
  --output runs/phase2-baseline-walk-forward.json
```

Use a fresh registry path for a clean reproduction; duplicate experiment IDs
are intentionally rejected.

