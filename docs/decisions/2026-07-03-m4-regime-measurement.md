# M4 — Regime Classifier (Measurement Only)

## What it is

`full_python.regime` tags every CME session with features fully decidable
at that session's 9:30 ET open — nothing computed after the open leaks in:

| Axis | Definition | Source |
|---|---|---|
| `adx` | daily ADX(14) through the prior session; `non_trending` < 20 | MR contract principle #5 |
| `variance_ratio` | VR(q=10) on prior-session RTH 1m log returns; < 0.9 mean-reverting, > 1.1 trending | MR contract principle #5 |
| `gap` | RTH open vs prior RTH close in daily-ATR units; flat < 0.10 | — |
| `prior_vol` | prior-session RTH 1m return stdev, full-sample terciles | — |
| `overnight_range` | overnight (18:00→9:30) range in daily-ATR units, terciles | — |

Attribution joins a run's trades to session tags: n, net, mean AND median,
win rate, Welch t vs all other trades, and an explicit `proven_sample`
flag at the n ≥ 50 threshold.

**This module never gates Adaptive Trend.** Every regime filter tested
inside the 9:30–10:00 window degraded net P&L; the window is the filter.
Regime tags exist to (a) describe where AT's P&L comes from and (b) feed
the MR sleeve's permission layer in M5 — a different strategy.

## First real-data attribution (flat core, 129 trades, 191 sessions)

**Result: no axis is statistically significant — every |t| < 2.0.** AT's
edge is not measurably regime-conditional at this sample size. This is
the expected result and independently confirms the April 2026 conclusion
("regime filters exhausted") with fresh tooling on fresh data. The
closest-to-signal cell — VR-trending days, net −$2,625 — is n=22,
t=−1.63: unproven, and no action follows from it.

Every bucket's median trade is ≈ −$650 (one stop): the median trade
loses; the mean is carried by right-tail winners. Any per-bucket mean at
n < 50 is dominated by whether a $5–10K winner landed in it.

## What M5 (mean reversion) learns from this

- ADX(14) < 20 admits ~28% of sessions (45/163 tagged) — the MR sleeve's
  strict trend gate will keep it out of the market roughly 3 days in 4,
  which is the intended shape for an uncorrelated sleeve.
- VR tags 57% of sessions mean-reverting on prior-session microstructure
  (bid-ask bounce inflates this; threshold calibration belongs to M5's
  run budget, not here).
- The fire-day overlap and daily-correlation machinery the MR contract
  requires (corr within ±0.2, ≤30% win-day overlap) can now be computed
  directly from tagged daily P&L series.
