# MR Variant 1 — VWAP Reversion v0.2-py, Run 2 (hypothesis)

Filed BEFORE the run. Run 2 of max 3 for this variant.

## Run 1 verdict (recorded first)

FAIL on the pre-set signal-validity criterion: WR 32.7% (required ≥ 55%),
PF 0.759, t = −4.0 over 1,156 trades — a significantly negative edge.
Diagnosis: the extremity unit was mis-scaled. 2.5 × ATR(14) on 1-minute
bars ≈ 20–25 NQ points from VWAP, which ordinary directional drift crosses
several times per day (6 trades/day; 763 stops vs 349 targets = fading
ordinary drift, not extremes). The exits behaved exactly as designed, and
the structural-decorrelation design PASSED: daily corr with AT = −0.036.

Contract note: the prescribed run-2 theme is "exit/stop fix", but run 1's
failure mode is unambiguously the entry unit, and the run-1 hypothesis
pre-committed to "below 50% WR → attack the entry, not the exits".
Deviation documented per contract.

## The one change

Band unit becomes the literature-standard **volume-weighted VWAP sigma**:

    sigma^2 = (Σ vol·typical²)/(Σ vol) − VWAP²   (session-cumulative)

Entry when close extends ≥ 2.5σ from VWAP. This sigma grows with the
session's own dispersion (≈ √t), so "extreme" self-calibrates per day —
quiet days need small absolute moves, wild days need large ones. Everything
else is UNCHANGED from run 1 (stop 1×ATR, static 2:1 target, 20-bar time
stop, ADX<20 gate, 10:00–15:30 window, cooldown 5): one variable moved,
so the delta is attributable.

## Pre-set evaluation criteria — identical to run 1

WR ≥ 55%; don't-give-up at PF ≥ 1.2 with |t| ≥ 2.0; portfolio trigger at
positive net with |corr(AT)| ≤ 0.2; floor pace ≈ $33K/8mo. Expected trade
count drops an order of magnitude (2.5σ bands are touched rarely); if
n < 30 the run is judged qualitatively and the band multiple becomes the
first run-3 sweep axis.
