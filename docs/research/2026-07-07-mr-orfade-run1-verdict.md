# MR Variant 2 — Opening Range Fade, Run 1 Verdict

Filed after executing the pre-registered Run 1 design in
`docs/research/2026-07-06-mr-orfade-run1-hypothesis.md`.

## Status

**Rejected on train. Holdout not evaluated.**

The opening-range-fade concept did not show a positive edge on the locked
train window. It failed the pre-set iterate / edge-found criteria by a
wide margin:

- PF is below `1.2`.
- Per-trade and per-session t-statistics are significantly negative.
- Long and short sides are both negative.
- Removing top trades makes an already-negative result worse.
- Daily correlation to Adaptive Trend is low, but that does not matter
  without positive expectancy.

## Data And Assumptions

- Strategy: `opening_range_fade`
- Input data: `runs/mr-orfade-run1-train/input_2022-11-01_to_2025-07-01.csv`
- Run output: `runs/mr-orfade-run1-train/run/`
- Scored train window: `2023-01-01T00:00:00Z` to `2025-07-01T00:00:00Z`
- Warmup/input window: `2022-11-01T00:00:00Z` to `2025-07-01T00:00:00Z`
- Point value: `$20/pt`
- Commission: `$10` round trip
- Entry slippage: `0.75` points
- Exit slippage: `0.75` points
- RTH-open extra entry slippage: `0.0`
- Fill timing: next-bar open

The run uses the same frozen NQ cost model used by the Python-era Gate 1
research.

## Train Results

| Metric | Result |
|---|---:|
| Trades | 539 |
| Net P&L | -$30,050 |
| Profit factor | 0.692 |
| Win rate | 29.87% |
| Expectancy / trade | -$55.75 |
| Trade-level t-stat | -3.74 |
| Sessions with trades | 161 |
| Session-level t-stat | -3.96 |
| Max drawdown | -$32,225 |
| Max loss streak | 13 |
| Avg R | -0.300 |
| Median R | -1.093 |
| Daily corr vs Adaptive Trend | -0.012 |

## Side Split

| Side | Trades | Net P&L | Win Rate | PF |
|---|---:|---:|---:|---:|
| Long | 257 | -$12,810 | 31.91% | 0.735 |
| Short | 282 | -$17,240 | 28.01% | 0.649 |

There is no useful directional asymmetry. Both sides are negative, and
shorts are worse.

## Exit Reason Split

| Exit | Trades | Net P&L |
|---|---:|---:|
| Stop | 372 | -$97,145 |
| Target | 152 | $66,030 |
| Time stop | 15 | $1,065 |

The 2R target did not compensate for the stop frequency. This is the
core mechanism failure: the signal is not selecting failed extensions
that revert often enough.

## Outlier Check

| Cut | Net P&L |
|---|---:|
| No cut | -$30,050 |
| Remove best 1 | -$31,215 |
| Remove best 3 | -$33,315 |
| Remove best 5 | -$35,260 |

There is no hidden right-tail rescue. The branch is negative before and
after top-trade removal.

## Year Split

| Year | Net P&L |
|---|---:|
| 2023 | -$14,460 |
| 2024 | -$11,655 |
| 2025 train | -$3,935 |

The failure is not isolated to one year.

## Pre-Registered Criteria

Run 1 criteria from the hypothesis:

- Iterate if PF `>= 1.2` with `|t| >= 2.0` on train.
- Iterate if strong directional asymmetry with `|t| >= 2.0`.
- Iterate if modest positive edge with daily corr to AT `<= 0.2`.
- Edge-found if net `>= $150K` and PF `>= 1.3`, or net `>= $75K` with
  corr `<= 0.2`.
- Toward closing if PF `< 1.2`, `|t| < 2.0`, and no directional
  asymmetry.

Strictly speaking, the result is worse than the "toward closing" case:
the t-statistics are not merely insignificant, they are significantly
negative.

## Decision

**Reject Run 1. Do not touch holdout.**

This variant should not proceed to calibration. A Run 2 would need a new
pre-filed mechanism fix, not parameter tuning around this result.

The most likely mechanism read is:

> On NQ, a failed opening-range extension after 10:00 is not reliably a
> mean-reversion opportunity under this ATR bracket. Too many failures
> continue or chop enough to hit the 1R stop before paying the 2R target.

## Next Research Guidance

Do not sweep this configuration. The failure is not close.

Only consider Run 2 if the design changes the mechanism, for example:

- require a stronger exhaustion signature before fading,
- require return inside the range plus rejection from VWAP / midpoint,
- restrict to a narrower time-of-day zone,
- or convert the idea into a diagnostic feature for Adaptive Trend
  rather than a standalone sleeve.

Until such a mechanism is pre-filed, the mean-reversion track should stay
paused and the project should return to the live-execution roadmap.
