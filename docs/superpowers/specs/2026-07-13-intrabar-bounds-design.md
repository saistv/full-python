# Phase 2 Intrabar Bounds Design

## Question

How much of the Adaptive Trend result depends on unknown price ordering inside
one-minute bars, especially trades stopped during their entry minute?

## Scope

The frozen simulator remains stop-first and its P&L is not changed. For every
path-ambiguous stop exit, the existing confirmed MFE is the lower bound. The
favorable extreme of the exit bar is the OHLC upper bound. The true pre-stop
MFE lies somewhere inside that interval unless tick or lower-timeframe sequence
data resolves it.

The report measures:

- stop and entry-minute-stop counts and P&L;
- path-ambiguous exit count and P&L;
- aggregate, median, and maximum MFE interval width;
- counts whose MFE classification is unresolved at 5, 10, 15, 20, 30, and
  40-point thresholds;
- missing exit bars, which fail the authority run rather than silently dropping
  trades.

## Interpretation Boundary

Adaptive Trend has no profit target. Therefore, a stop-bar favorable extreme
does not alter the frozen stop exit or reported P&L; it only affects MFE and any
counterfactual gate derived from MFE. Exact MFE claims remain prohibited for
ambiguous rows. Resolving them requires tick or sub-minute sequence data.

