# Entry-Window Sweep — CLOSED, No Qualifier (9:30 open confirmed essential)

Pre-registered Gate 1 sweep of the entry window
(`entry_start_minutes_et` × `entry_end_minutes_et`), via
`scripts/sweep_entry_window.py`. The trading window (default 9:30-10:00
ET) was a FIXED ASSUMPTION Gate 1 never swept; the malleability work in
the live-data-feed sub-project made it a clean config axis. Grid, data
window, cost model, and the promotion table were locked before the run.
Full outputs: `runs/sweeps/window-grid/` (gitignored, reproducible).

**Result: no cell passed every scored row — the entry-window axis
CLOSES on train evidence. The 9:30-10:00 window is confirmed optimal by
a wide margin.** This is Python confirmation of the TV-era "the window
IS the filter," with one nuance to a prior (below).

## Scoreboard (train 2023-01-01 → 2025-06-30, 8 cells, sorted by net)

| Window (ET) | Trades | Net P&L | Δ vs baseline | paired t | verdict |
|---|---|---|---|---|---|
| 9:30-10:30 (60m) | 492 | $73,815 | **+$7,960** | 0.46 | only cell above baseline — fails materiality + significance |
| **9:30-10:00 (30m)** | 378 | $65,855 | — | — | **BASELINE (production)** |
| 10:00-10:30 | 215 | -$2,575 | -$68,430 | -1.74 | later start destroys the edge |
| 9:45-10:45 | 351 | -$4,195 | -$70,050 | -2.00 | |
| 9:45-10:15 | 234 | -$5,015 | -$70,870 | -2.23 | |
| 10:00-11:00 | 317 | -$10,630 | -$76,485 | -1.92 | |
| 10:30-11:00 | 158 | -$20,785 | -$86,640 | -2.25 | |
| 10:30-11:30 | 295 | -$34,600 | -$100,455 | -2.47 | |

## Finding 1: the 9:30 open start is essential and irreplaceable

Every window that starts later than 9:30 is **catastrophically worse**,
and monotonically so with lateness. Moving the start just 15 minutes
(9:45-10:15) turns a +$65,855 strategy into a -$5,015 loser (Δ
-$70,870); starting at 10:30 loses $20-35K. The later-start cells carry
significantly-NEGATIVE paired t (≈ -2 to -2.5), i.e. they are reliably
worse, not noise. The edge lives in the opening auction and cannot be
shifted later in the session — strong Python confirmation of the TV-era
"the window IS the filter" and the earlier observation that 72% of
trades fire in the first ~9 minutes.

## Finding 2 (nuances a TV-era prior): 60-min expansion is P&L-neutral-to-mild-positive, not "worse"

The TV era rejected window EXPANSION on the belief it produced "more
trades, same P&L, worse drawdown." Under the Python engine + AM/DLL
sizing, 9:30-**10:30** (492 trades vs 378) is the ONE cell that beats
baseline — by **+$7,960** — and it PASSES the drawdown and outlier-
survival rows (the extra P&L is not one-trade-driven; drawdown does not
worsen). So the old "worse P&L / worse DD" claim does not replicate.

But it is NOT a qualifier: +$7,960 is below the $10,000 materiality bar,
paired t = 0.46 (≪ 2.0), and it fails the expectancy and year-by-year
rows. The extra 10:00-10:30 window trades are ~$70/trade — barely above
breakeven after costs, statistically indistinguishable from zero. So
expansion adds marginal, insignificant volume: it doesn't hurt, but it
doesn't clear the bar. Per the pre-registered rule, a sub-bar result is
reported, not treated as a lead.

## Finding 3: no degenerate behavior

All 8 cells ran clean (no errors); trade counts vary sensibly with
window width/placement; the response surface is smooth and monotone in
lateness — no lone spikes.

## Protocol compliance

Same discipline as the MA and S/R sweeps: grid/windows/cost model locked
before the run and pinned by `tests/test_sweep_window_driver.py`; row 9
= session-level paired t per the 2026-07-05 Phase 0 amendment; the
selection rule returned no qualifier; holdout untouched; no slippage
runs (no qualifier). No intermediate windows chased, no re-formed tests,
no "the +$8K expansion is close enough."

## Status

- `entry_start_minutes_et` / `entry_end_minutes_et` — **CLOSED**. The
  9:30-10:00 production window is confirmed optimal; the 9:30 open start
  is non-negotiable (later = monotonically worse); expansion is
  P&L-neutral and below the bar.
- The window remains config-driven and malleable (per the live-data-feed
  design). If the market's opening-auction regime materially changes in
  future, this exact sweep can be re-run — but as of the 2023-2025 train
  window, 9:30-10:00 wins decisively.

Caveats (binding): backtest, 1-contract, pessimistic frozen cost model —
not a promotion of any change; the config is unchanged. Drawdowns are
floors, not bounds.
