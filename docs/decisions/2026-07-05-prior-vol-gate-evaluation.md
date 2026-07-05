# Prior-Session-Volatility Gate — Gate 1 Evaluation (REJECTED)

Evaluates the `enable_prior_vol_gate` feature (built in
`docs/superpowers/plans/2026-07-05-prior-vol-gate.md`, merged to
`claude/m4-regime`) against the full promotion table locked in
`docs/decisions/2026-07-05-gate1-phase0-protocol.md`, using the
corrected Phase 2 diagnosis numbers
(`docs/decisions/2026-07-05-gate1-phase2-diagnosis.md`).

**Result: REJECTED.** The candidate clears every row of the train-window
promotion table — impressively — and then fails on holdout with a sign
reversal. Per Phase 0's locked rule, this is a rejection with no
re-running, re-slicing, or re-parameterizing to recover a pass. This
document exists to record the finding as real research output, not to
hide the miss.

## Setup

Both runs use the full assembled 5-year continuous series
(`runs/multi-year/nq1_2021-03-16_2026-06-26.csv`, 1,871,670 bars,
2021-03-16 → 2026-06-26) for state continuity (AM win-streak, DLL
budget carry across the whole history exactly as they would in
production), with the frozen cost model
(`point_value=20, commission_rt=10, entry_slippage_points=0.75,
exit_slippage_points=0.75, rth_open_extra_entry_slippage_points=0.0`).
Baseline = `production_am_config()` unchanged. Candidate = the same
config with `enable_prior_vol_gate=True` (all other fields, including
`prior_vol_high_threshold=0.0004638315483775433`, at their shipped
defaults). Trades are then sliced into train (2023-01-01 → 2025-06-30)
and holdout (2025-07-01 → 2026-06-26) by entry timestamp.

## Train-window promotion table (all 9 rows required)

| # | Row | Baseline | Candidate | Result |
|---|---|---|---|---|
| 1 | Net P&L improves + $10K materiality bar | $65,855 | $90,150 (+$24,295) | ✅ clears bar |
| 2 | Expectancy improves ≥10% | $174.22/trade | $360.60/trade (+107%) | ✅ |
| 3 | Trade count doesn't drop >20% unless justified | 378 | 250 (-33.9%) | ✅ **justified** — see below |
| 4 | Max drawdown doesn't worsen >15% | -$13,985 | -$11,725 (improved) | ✅ |
| 5 | Survives top-1/2/3 outlier removal | $52,575 / $43,050 / $34,580 | $80,625 / $72,155 / $63,770 | ✅ candidate stays ahead at every cut |
| 6 | ≥2 of 3 years better-or-neutral | 2023: -$9,085; 2024: $47,365; 2025(H1): $27,575 | 2023: **+$7,735**; 2024: $57,370; 2025(H1): $25,045 | ✅ 2023 & 2024 better, 2025(H1) slightly worse |
| 7 | Not carried by one side only | long $37,520 / short $28,335 | long $51,680 (+$14,160) / short $38,470 (+$10,135) | ✅ both sides improve |
| 8 | Survives slippage sensitivity (0.5pt, 1.0pt) | 0.5pt: $70,025; 1.0pt: $62,210 | 0.5pt: **$93,000**; 1.0pt: **$87,825** | ✅ candidate ahead at both |
| 9 | Welch \|t\| ≥ 2.0 | — | t = -2.762 (high-vol bucket vs. rest, Phase 2 diagnosis, same 642-session train population) | ✅ |

**Row 3 justification, verified not assumed:** 129 trades were removed by
the gate on train; 128 of them (99.2%) fall on a session already tagged
"high prior-day volatility" by the Phase 2 diagnosis, and their combined
net P&L (-$23,270) closely matches the diagnosed bucket's own net P&L
(-$22,610) — the small delta is AM win-streak re-sequencing (removing an
entry changes the win-streak state for nearby entries), the same
cascading effect flagged in `feedback_pyramiding_rejected.md`, not an
unexplained discrepancy. This is the opposite of Standard 7's warning
(a filter blocking positive-EV trades to inflate PF) — the removed
population was independently proven negative-EV before this evaluation
ever ran.

**Every row passes.** On train alone, this looks like a strong,
well-evidenced candidate — exactly why Phase 0 mandates a one-shot,
no-recovery holdout check before treating any train-only result as
proof.

## Holdout (touched exactly once, per Phase 0's locked rule)

| | Baseline | Candidate | Delta |
|---|---|---|---|
| Trades | 154 | 104 | -50 |
| Net P&L | $51,790 | $28,960 | **-$22,830** |

Train's delta was **+$24,295**. Holdout's delta is **-$22,830** — a sign
reversal. Per Phase 0: *"A holdout result that reverses sign fails the
candidate regardless of train performance — the holdout is not re-run,
re-sliced, or re-parameterized to try to recover a pass."*

**Why it reversed, verified not assumed:** the 50 trades the gate
blocked on holdout had a combined net P&L of **+$17,880** — the gate
blocked genuinely profitable trades this time, not negative-EV ones. The
removed trades' win rate (16%) was actually lower than baseline's
overall holdout win rate (22.7%), but their wins were large: five of the
removed trades individually netted over $5,000, one over $10,000. This
is the same right-tail shape Phase 2's diagnosis and the `strategy-audit`
skill's Standard 12 describe for the strategy as a whole ("the edge IS
the right tail... winners dip before running 50-200pt") — on holdout,
the high-prior-vol regime happened to correlate with the strategy's best
trades instead of its worst ones, the opposite of the train-window
relationship.

## Conclusion

**REJECTED.** The train-window relationship between prior-day realized
volatility and next-session P&L, while statistically significant and
robust to every train-window check (materiality, expectancy, drawdown,
outlier removal, year-by-year, long/short symmetry, slippage
sensitivity), did not generalize to the holdout window — a genuine
regime non-stationarity, not a data error or implementation bug. Per
Phase 0's explicit failure criteria, this candidate does not get
re-tried with a different threshold, a different window split, or a
narrower definition of "high volatility." The feature code
(`enable_prior_vol_gate`) stays in the codebase, defaulted off, as a
correctly-built and now-evaluated option — not deleted, since a properly
rejected candidate is itself useful research history — but it is **not
promoted to production** and must not be turned on.

## What this validates about the process, not just the result

This is exactly the outcome the pre-registered protocol exists to catch.
Had this evaluation stopped at the train-window promotion table (as a
less disciplined analysis might), every single row would have read as a
green light — net P&L up 37%, expectancy up 107%, drawdown improved,
survives outliers, survives slippage stress, statistically significant.
The one-shot holdout check is what prevented shipping a config change
that would have cost real money on the very data it was meant to
protect against. This is direct evidence for keeping the discipline on
every future Gate 1 candidate, not a reason to loosen it because "this
one looked so good on train."

## Artifacts

`runs/prior-vol-gate-candidate/trades.csv` (gitignored, reproducible via
the setup above). Baseline trades already exist at
`runs/multi-year-backtest/trades.csv` (from the Gate 1 Phase 2 diagnosis
run).
