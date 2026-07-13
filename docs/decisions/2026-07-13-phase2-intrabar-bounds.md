# Phase 2 NQ Intrabar MFE Bounds

**Decision:** one-minute path ambiguity does not affect the frozen Adaptive
Trend P&L replay, but it materially limits low-threshold MFE counterfactuals.
Phase 2 robust experimentation is complete. Exact MFE-based permissions for
ambiguous trades remain prohibited without tick or sub-minute sequence data.

## Registered Design

- Experiment: `phase2-nq-intrabar-bounds-v1`
- Trial budget: 1 measurement run
- Clean source commit: `a6c4f47`
- Five-year NQ authority data and frozen execution assumptions
- Confirmed MFE before a stop bar is the lower bound
- The stop bar's favorable OHLC extreme is the upper bound
- Missing or duplicate join bars fail closed
- No strategy, stop, exit, target, or sizing changes

Adaptive Trend has no profit target. Once a one-minute bar reaches its stop,
the frozen stop-first replay exits at that stop regardless of whether the bar's
favorable extreme occurred before or after it. Price ordering changes MFE, not
P&L, for this strategy.

## Results

| Measure | Result |
|---|---:|
| Total trades | 813 |
| Stop exits | 621 |
| Entry-minute stop exits | 65 |
| Entry-minute stop P&L | -$39,325 |
| Path-ambiguous stop exits | 59 |
| Ambiguous exit P&L | -$34,985 |
| Ambiguous exits occurring in entry minute | 53 |
| Confirmed MFE lower-bound total | 56.75 points |
| OHLC MFE upper-bound total | 371.75 points |
| Total uncertainty width | 315.00 points |
| Median uncertainty width | 3.50 points |
| Maximum uncertainty width | 27.50 points |
| P&L path-uncertain trades | 0 |

Entry-minute stops are 8.0% of all trades and 10.5% of stop exits. They are a
real source of strategy losses, but one-minute data cannot reveal how much
favorable movement occurred before the stop inside those bars.

## MFE Threshold Sensitivity

| Hypothetical MFE threshold | Trades with unresolved classification |
|---|---:|
| 5 points | 21 |
| 10 points | 12 |
| 15 points | 4 |
| 20 points | 4 |
| 30 points | 0 |
| 40 points | 0 |

A trade is unresolved when its confirmed lower bound is below a threshold but
its OHLC upper bound reaches or exceeds it. These counts do not say the trade
actually reached the threshold before stopping. They say one-minute OHLC
cannot decide.

## Consequence

- Current net P&L, stop count, and drawdown remain decision-grade under the
  declared stop-first model.
- Exact MFE/MAE claims remain invalid for the 59 flagged rows.
- Any proposed add-on, breakeven, or risk-permission rule using a 5–20 point
  MFE gate must either resolve these rows with sequence data or report both
  lower-bound and upper-bound outcomes.
- Thresholds at 30 points and above are not crossed solely because of the
  ambiguous stop-bar extreme in this dataset.
- The `$39,325` lost by entry-minute stops is not automatically recoverable.
  Treating every favorable OHLC extreme as occurring first would be lookahead.

Artifacts (gitignored):

- `runs/phase2-nq-intrabar-bounds-v1.json`
- `runs/phase2-intrabar-bounds-v1.sqlite`

