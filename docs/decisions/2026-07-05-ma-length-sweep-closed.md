# MA-Length Sweep (ma_50 × ma_200) — CLOSED, No Qualifier

First run of the Gate 1 Phase 4 sweep harness
(`scripts/sweep_ma_lengths.py`, built per
`docs/superpowers/specs/2026-07-05-sweep-harness-design.md`). Grid,
data window, cost model, scoring rows, and the selection rule were all
pre-registered before any cell ran. Full outputs:
`runs/sweeps/ma-grid/` (gitignored, reproducible).

**Result: no cell passed every scored row. Per the pre-registered
rule, the `ma_50_length` × `ma_200_length` axis pair CLOSES on train
evidence alone. No cell advances to slippage runs or holdout.**

## Scoreboard (train window 2023-01-01 → 2025-06-30, 25 cells)

Results collapse to 5 distinct outcomes because `ma_50_length` had
zero effect (see finding 1):

| ma_200 | n | net | delta | paired t | rows passed | verdict |
|---|---|---|---|---|---|---|
| 100 | 394 | $83,940 | **+$18,085** | **1.75** | 6 of 7 (fails t) | below significance bar |
| 150 | 383 | $70,780 | +$4,925 | 0.93 | 3 of 7 | fails materiality |
| 200 (baseline) | 378 | $65,855 | — | — | — | — |
| 250 | 371 | $59,995 | -$5,860 | -0.63 | 1 of 7 | worse |
| 300 | 364 | $62,425 | -$3,430 | -0.37 | 1 of 7 | worse |

## Finding 1: ma_50_length is non-binding across 30–70

All five `ma_50_length` values produced **byte-identical trade lists**
within every `ma_200_length` row (verified by MD5 of the per-cell
trades.csv files, e.g. `ma50_30_ma200_200` ≡ `ma50_70_ma200_200`).
The override mechanism itself works — `ma_200_length` changes shift
trade counts 394→364 — so this is a real property of the strategy, not
a harness bug: by the time the full entry stack fires (ATF flip +
squeeze + S/R break + prove-it + close beyond the 200-SMA), price is
always on the correct side of every EMA in the 30–70 range. Within
this range the ma_50 filter is redundant on this window.

This is NOT evidence for removing the filter: values outside 30–70
(or OFF) were not in the registered grid and were not tested. If that
question ever matters, it is a new registered experiment, not an
extension of this one.

## Finding 2: the ma_200=100 near-miss stays a near-miss

`ma_200_length=100` cleared 6 of 7 scored rows — materiality
(+$18,085), expectancy, drawdown, outlier survival at all three cuts,
year-by-year, side symmetry — and failed exactly one: session-level
paired t = 1.75 < 2.0.

Per the locked Phase 0 protocol: *"Below this bar: reported as 'not
significant,' not treated as a lead."* That sentence was written for
precisely this moment. The temptations it forbids, enumerated so they
are on the record as NOT taken:

- No trying intermediate values (ma_200 ∈ {110, 120, 130}) to chase
  the gradient — that is adaptive refinement around a peak, excluded
  by the pre-registration.
- No re-slicing sessions or re-forming the t-test until it clears 2.0.
- No "it passed 6 of 7, close enough" — the promotion table is
  all-rows-required by construction.
- No holdout peek "just to see" — holdout is spent only on a full
  qualifier, and there is none.

The monotone train gradient (shorter ma_200 → more trades → more net
P&L: 100 > 150 > 200 > 250 ≈ 300) is genuinely interesting, and t=1.75
on ~630 paired sessions is suggestive. But the prior-vol gate
(`docs/decisions/2026-07-05-prior-vol-gate-evaluation.md`) cleared a
FAR stronger train case — every row, |t|=2.76 — and still reversed
sign on holdout. The bar exists because train-window suggestion is
cheap. A future case for a shorter trend filter would need new
evidence (e.g. the same effect appearing independently in a different
registered experiment), not a re-run of this one.

## Protocol compliance

- Grid, windows, cost model: exactly as registered in the design spec
  and pinned by `tests/test_sweep_driver.py`.
- Row 9 statistic: session-level paired t per the 2026-07-05 protocol
  amendment (recorded before this sweep ran).
- Selection rule executed as coded: `select_qualifier` returned None;
  nothing advances.
- Holdout: untouched. Slippage runs: not performed (no qualifier).

## Status of Phase 4 open axes after this sweep

- `ma_50_length` — **CLOSED** (non-binding 30–70; grid exhausted).
- `ma_200_length` — **CLOSED** (best off-default cell fails the
  significance bar; per protocol not a lead).
- `sr_min_stop_distance` × `sr_stop_buffer` interaction — still open,
  next sweep job (same harness, own pre-registered grid, and its own
  decision about re-verifying the TV-era single-axis findings).
- `fallback_stop_points` — already closed by Phase 2 diagnosis.
