# Gate 1 Phase 0 — Pre-Registered Protocol

**Locked 2026-07-05, before any Phase 4 sweep is run.** This document
freezes the experimental design — exact data splits, materiality bar,
axes to sweep, and promotion/failure criteria — so later sweep results
cannot retroactively reshape what counts as success. If a result doesn't
clear the bar set here, the bar does not move; the candidate is
rejected, full stop.

This is the Python-side successor to the TV-era promotion bar. Per the
Python Reference Engine Migration decision, TV confirmation is no longer
required for promotion — the frozen Python Baseline Anchor
(`docs/decisions/2026-07-04-python-baseline-anchor.md`) is the reference.
All 14 non-TV-specific standards from the `strategy-audit` skill (sample
size, significance testing, outlier sensitivity, no single-trade
conclusions, no re-litigating closed axes, real-money evidence bar) still
govern every claim below.

## Data

Full continuous NQ series assembled 2026-07-05 from Databento GLBX
`ohlcv-1m`: `runs/multi-year/nq1_2021-03-16_2026-06-26.csv`, 1,871,670
bars, structurally validated (zero data-quality issues), 22 correctly
identified quarterly rolls. Strategy: `adaptive_trend_am`
(`production_am_config()`). Cost model: identical to the frozen anchor —
`point_value=20, commission_rt=10, entry_slippage_points=0.75,
exit_slippage_points=0.75, rth_open_extra_entry_slippage_points=0.0`.

**Splits (by trade entry timestamp, fixed before any diagnosis or sweep):**

| Split | Window | Trades | Net P&L | Purpose |
|---|---|---|---|---|
| Buffer (unused for Gate 1 decisions) | 2021-03-16 → 2022-12-31 | 297 | $41,515 | Warmup history + optional future extended-robustness reference only. Not used in any promotion decision below. |
| **Train** | 2023-01-01 → 2025-06-30 | **378** | $65,855 | All sweep decisions (Phase 2 diagnosis, Phase 4 sweeps) run on this window only. |
| **Holdout** | 2025-07-01 → 2026-06-26 | **154** | $51,790 | Touched exactly once, after a candidate has already cleared every train-window row of the promotion table (Phase 5). Contains the entire frozen 9-month Baseline Anchor window (2025-10-01 → 2026-06-26) as a subset. |

Both train (378) and holdout (154) clear the n≥50 proven-sample floor
`regime.py` already enforces (`MIN_PROVEN_TRADES = 50`).

## Materiality bar (locked)

A candidate config change must, on the **train** window, satisfy:
- `|net_pnl_delta| >= $10,000`, AND
- Welch `|t| >= 2.0` on the per-trade net P&L distributions of
  candidate vs. baseline (`full_python.regime.welch_t`), reported as the
  preferred statistic — Welch's test does not assume equal variances
  between the two configs' trade populations, which two backtests with
  different trade counts and different win/loss size mixes are not
  guaranteed to share; a pooled-variance (Student's) t-test would be
  reported alongside if requested, never in place of Welch.

Below this bar: reported as "not significant," not treated as a lead.

## OPEN axes for Phase 4 (from the migration plan's Phase 3 axis map)

Only these fields of `AdaptiveTrendConfig` are open for sweeping. Every
other field has prior closed-sweep evidence (S/R pivot geometry, break
lookback, wings calibration, BE-stop threshold, DLL, ATF length,
cooldown bars, squeeze internals — see the `strategy-audit` skill's
Validated Rules table and the corresponding `feedback_*` memory records)
and is **not** re-opened here:

- `ma_50_length` (default 50) — confirmed OPEN, never swept in either
  the TV or Python engine.
- `ma_200_length` (default 200) — confirmed OPEN, same as above.
- `fallback_stop_points` (default 30.0) — **conditionally** open: TV-era
  research found 30pt superior to 25pt in the Pine backtester
  (`feedback_...wide_open_audit`), but that comparison predates the
  Python engine and was never re-verified against `adaptive_trend_am`'s
  actual fallback-stop usage frequency in Python. Phase 2's diagnosis
  (below) determines whether this axis is worth sweeping at all: if the
  fallback stop is rarely or never the binding stop in the train window,
  sweeping it is not a meaningful use of the materiality bar's budget.
- `sr_min_stop_distance` × `sr_stop_buffer` interaction — explicitly
  flagged by the plan as an interaction to check jointly, not just as
  two independent single-axis sweeps (Standard 15: single-dimension
  sweeps miss interaction effects — this is the one axis pair the plan
  names as a known risk for exactly that failure mode).

## Promotion table (Phase 5 — every row required, no partial credit)

A candidate must clear ALL of the following on the train window before
holdout is touched:

1. Net P&L improves (`net_pnl_delta > 0`) and clears the materiality bar above.
2. Expectancy per trade improves by ≥10% (`metrics.expectancy.expectancy_dollars`).
3. Trade count does not drop by >20% unless the drop is itself explained
   and justified (a filter that blocks positive-EV trades to raise PF is
   not an improvement — Standard 7).
4. Max drawdown does not worsen by >15%.
5. Survives removal of the top 1, top 2, and top 3 trades by net P&L
   (conclusion must not flip — Standard 4).
6. ≥2 of 3 train-window calendar years (2023, 2024, first half of 2025)
   are better-or-neutral, not carried entirely by one year.
7. The improvement is not carried by one side only (long-only or
   short-only gains with the other side flat/worse fails this row).
8. Survives a slippage-sensitivity check at 0.5pt and 1.0pt entry/exit
   slippage (not just the 0.75pt baseline).
9. Welch `|t| >= 2.0` (already required by the materiality bar, restated
   here as a promotion-table row so it can't be dropped silently in a
   later summary).

**Holdout rule:** once a candidate clears rows 1-9 on train, it is run
on holdout exactly once. The holdout result must be same-sign (net P&L
improvement direction matches train's). A holdout result that reverses
sign fails the candidate regardless of train performance — the holdout
is not re-run, re-sliced, or re-parameterized to try to recover a pass.

## Explicit failure criteria (stated before results exist)

A candidate is REJECTED, not "inconclusive," if any of:
- Materiality bar not cleared on train.
- Any single promotion-table row (1-9) fails.
- Holdout result reverses sign vs. train.
- The only positive result is driven by <50 trades in a subgroup
  (Standard 1/9 — assume coincidence).
- Removing the single largest winning trade in the candidate's favor
  flips the conclusion (Standard 4).

## Explicitly out of scope for this Phase 0

- Any exit-mechanism modification (partial exits, tightened stops, Quick
  Kill, profit caps, BE-stop threshold changes) — all closed per the
  Validated Rules table; re-proposing them is a Standard 12 violation.
- Any change to `sr_left_bars`, `sr_right_bars`, `sr_break_lookback`,
  `wings_body_atr_frac`, `wings_close_frac`, `wings_atr_length`,
  `sqz_*` fields, `atf_length`, `atf_smooth`, `atf_sensitivity`,
  `stop_loss_cooldown_bars`, `entry_cooldown_bars`,
  `breakeven_exit_cooldown_bars`, `max_stop_distance`,
  `sr_break_lookback`, AM/DLL parameters (`max_contracts_per_entry`,
  `daily_loss_limit`, `dll_risk_buffer`) — all CLOSED per prior sweeps.
- Contract/instrument selection (NQ vs MNQ, 1 vs 2 contracts) — that is
  the Capital Allocation Gate's domain
  (`docs/decisions/2026-07-04-sizing-research-gate.md`), run separately
  and after the raw 1-contract edge is proven, never mixed with edge
  research per the plan's explicit sequencing.
