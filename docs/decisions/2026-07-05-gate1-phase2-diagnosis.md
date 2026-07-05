# Gate 1 Phase 2 — Diagnosis (Train Window)

Run against the train window locked in `docs/decisions/2026-07-05-gate1-phase0-protocol.md`
(2023-01-01 → 2025-06-30, 378 trades, $65,855 net, `adaptive_trend_am`,
frozen cost model). All standards from the `strategy-audit` skill apply.

## 1. Right-tail concentration

| Cut | Net P&L | % of total |
|---|---|---|
| Total (378 trades) | $65,855 | 100% |
| Top 1 trade | $13,280 | 20.2% |
| Top 3 trades | $31,275 | 47.5% |
| Top decile (37 trades) | $189,820 | 288.2% |
| Without top 1 | $52,575 | — |
| Without top 3 | $34,580 | — |

**Outlier sensitivity (Standard 4): the conclusion survives.** Removing
the top 1, 2, or 3 trades leaves net P&L positive ($52,575 / $43,050 /
$34,580) — the edge is not *solely* dependent on 1-3 lucky trades, even
though it is heavily concentrated in the right tail (top decile alone
covers 288% of total P&L, meaning the bottom 90% of trades collectively
lose money). This matches the audit skill's Standard 12 rationale
verbatim ("the edge IS the right tail") with fresh, independent
confirmation on 378 Python-side trades rather than the original 448 TV
trades.

## 2. Exit-reason expectancy

| Exit reason | Trades | Net P&L | Win rate | Avg R |
|---|---|---|---|---|
| `stop` | 290 (76.7%) | -$173,465 | 0% | -1.05 |
| `atf_flip` | 45 (11.9%) | $74,540 | 88.9% | 3.22 |
| `session_flatten` | 39 (10.3%) | $154,905 | 94.9% | 6.35 |
| `session_end` | 4 (1.1%) | $9,875 | 100% | 3.92 |

Three-quarters of trades stop out at close to -1R (consistent, tight
loss control), and essentially all net P&L comes from the ~22% of
trades that survive to an `atf_flip` or `session_flatten` exit,
averaging +3 to +6R. This is the same right-tail shape as Standard 12
describes and is fully consistent with the Baseline Anchor's own
by-exit-reason breakdown on the shorter window
(`docs/decisions/2026-07-04-python-baseline-anchor.md`).

## 3. Fallback-stop usage-frequency gate (determines whether `fallback_stop_points` is worth sweeping)

Classified every train trade's `initial_risk_points` (`|entry_price -
stop_price|`, from `reporting/metrics.py`) against the three possible
stop-computation paths in `strategy/adaptive_trend.py`
(`_compute_long_stop`/`_compute_short_stop`):

| Stop path | Distance | Trades | % | Net P&L |
|---|---|---|---|---|
| Fallback (`fallback_stop_points=30`) | ≈30pt | 8 | 2.1% | -$4,715 |
| Max-capped (`max_stop_distance=31`) | ≈31pt | 33 | 8.7% | (not separately isolated) |
| Genuine dynamic S/R stop | varies | 337 | 89.2% | (majority of P&L) |

**Verdict: `fallback_stop_points` is NOT worth sweeping.** Only 8 of 378
trades (2.1%) actually use the literal fallback distance. Even in the
best possible case — the fallback stop is eliminated entirely and every
one of those 8 trades' losses is fully recovered — the maximum
achievable improvement is $4,715, which cannot clear the $10,000
materiality bar locked in Phase 0 before any t-test is even run. This
axis is **closed by diagnosis**, per Phase 0's own stated criterion, and
removed from the Phase 4 sweep plan. `sr_min_stop_distance` ×
`sr_stop_buffer` remains open and is now the higher-leverage stop-side
axis, since it governs the 89.2% majority dynamic-stop path.

## 4. Regime attribution (measurement only — `regime.py` never gates entries)

**Correction (2026-07-05, post-hoc, caught by the final whole-branch
review on the prior-vol-gate feature branch):** the numbers originally
reported in this section were computed with a methodology error —
`_assign_tags`'s tercile bounds were fit over 706 sessions spanning
2022-10-01 → 2025-06-30 (the train window PLUS a ~3-month pre-train
lookback buffer used to warm up the ADX/variance-ratio indicators),
not the train window alone. That leaks 64 pre-train sessions into the
threshold calibration — exactly the kind of leakage Phase 0 exists to
prevent ("all sweep decisions run on this window only"). The
`prior_vol_high_threshold` value later shipped in
`AdaptiveTrendConfig` (`0.0004638315483775433`) was, in fact, computed
correctly from the strict 642-session train-only population — so the
**code is correct**; this document's numbers were not, and are
corrected below. The underlying finding survives the correction (see
Conclusion), just with different exact figures — this is not a
retraction.

Ran `full_python.regime.compute_session_features` +
`_tercile_bounds` restricted to the train window's own 642 sessions
with a computable `prior_realized_vol` (2023-01-01 → 2025-06-30 only,
no pre-train sessions), then `full_python.regime.welch_t` per bucket
against train trades. Every bucket below clears the
`MIN_PROVEN_TRADES=50` floor.

**One result is statistically significant and survives outlier removal — flagged, not acted on:**

| `prior_vol` bucket | Sessions | Trades | Net P&L | Win rate | Mean | Median | Welch t vs. rest |
|---|---|---|---|---|---|---|---|
| High | 213 | 128 | **-$22,610** | **10.2%** | -$177 | -$645 | **-2.762** |
| Mid | 214 | 123 | $47,720 | 25.2% | $388 | -$530 | 1.445 |
| Low | 215 | 126 | $41,405 | 29.4% | $329 | -$478 | 1.119 |

`|t| = 2.762` clears the Phase 0 materiality bar's significance
threshold (`|t| >= 2.0`), on a proven sample (n=128), and the direction
is confirmed by both mean AND median (Standard 1) — not a case where
one statistic tells a nicer story than the other. **Outlier sensitivity
(Standard 4): removing the top 3 winning trades in the high-vol bucket
still leaves it deeply negative (-$46,885 net, i.e. MORE negative than
before removal, not less).** This is the opposite of what an
outlier-driven false positive would look like; it is strong evidence the
underperformance is systematic across the 128-trade population, not a
tail artifact.

**Why this is reported as a finding, not a recommendation:** `regime.py`'s
own module docstring states a hard rule — "This module NEVER gates
entries... Regime tags exist to describe where AT's P&L comes from" —
and the existing memory record `feedback_regime_filters_exhausted.md`
already closed the question of adding regime-based entry filters within
the 9:30-10:00 window ("Every regime filter tested within 9:30-10:00
degrades net P&L... Window IS the filter. Do not add layers."). That
prior work used an AER-based filter design, not a prior-day
realized-volatility filter specifically, so this is not a byte-for-byte
re-test of a closed axis — but it is close enough to the spirit of that
closed rule (a volatility/regime-conditioned entry gate) that it should
not be unilaterally proposed as an implementation change here. **This is
a decision for the user**, not something to act on by adding a filter:
the evidence is real and cleared the bar, but the prior base rate for
"promising-looking regime filter turns out to degrade live P&L once
implemented" is 100% in this strategy's history so far.

No other regime axis (`adx`, `variance_ratio`, `gap`, `overnight_range`)
produced `|t| >= 2.0` in either direction — all other buckets are
reported as not significant, per Standard 2, rather than described with
softer language that implies a pattern.

## Phase 3 axis map (carried forward into Phase 4, revised per this diagnosis)

- `ma_50_length`, `ma_200_length` — still OPEN, unaffected by this
  diagnosis (Phase 4 sweep proceeds as planned).
- `fallback_stop_points` — **CLOSED by diagnosis** (see §3 above), removed
  from Phase 4.
- `sr_min_stop_distance` × `sr_stop_buffer` interaction — still OPEN,
  now the primary stop-side sweep target (governs 89.2% of trades).

## Next: Phase 4

Sweep `ma_50_length`, `ma_200_length`, and the `sr_min_stop_distance` ×
`sr_stop_buffer` interaction grid, train-only, against the full
promotion table in Phase 0 — including per-cell trade-count-distortion
and year/quarter/long-short/window-half robustness checks on any cell
that clears the materiality bar.
