# Phase 1 Evidence Migration

**Decision:** Python is the sole performance authority. The historical
`$251K / PF 2.071 / 448 trades` TradingView headline and the old `$760/month`
prop-account estimate are retired. They are not baselines, promotion gates, or
capital-planning inputs.

No strategy parameter changed in this phase. This is a corrected measurement
of `production_am_config()` after Phase 0 fixed holidays, MFE/MAE path labels,
and NQ/MNQ dollar-risk authority.

## Standard Reporting Contract

Every CLI run now writes these fields into `report.json` and the HTML report:

- profit factor, win rate, and expectancy per trade;
- net after removing the top 1, 3, 5, and 10 trades;
- net after removing the top 1, 3, 5, and 10 days;
- top-five-day share of net profit;
- deterministic 10-session moving-block bootstrap with 2,000 draws;
- 95% intervals for total net, annualized net, and annualized daily Sharpe;
- median, p95-adverse, and p99-adverse maximum drawdown;
- p95 maximum losing-day streak and probability total net is nonpositive.

The fixed seed is `20260712`. Session blocks, rather than IID trades, preserve
short-run regime persistence. These intervals describe uncertainty around this
historical process; they are not untouched out-of-sample proof.

## Corrected Five-Year Authority

Data: 2021-03-16 through 2026-06-26, 1-minute continuous front-month NQ-quality
bars. Costs: 0.75 points per side and $10 NQ / $1 MNQ round-trip commission.

| Metric | NQ execution | MNQ execution |
|---|---:|---:|
| Trades | 813 | 859 |
| Quantity distribution | 751x1, 57x2, 5x3 | 675x1, 138x2, 31x3, 15x4 |
| Net P&L | $160,125.00 | $25,931.50 |
| Profit factor | 1.420 | 1.533 |
| Win rate | 22.1% | 21.5% |
| Expectancy/trade | $196.96 | $30.19 |
| Observed max drawdown | -$18,570.00 | -$2,865.50 |
| Max trade loss streak | 22 | 23 |
| Time underwater | 294 sessions | 532 sessions |
| Net without top 5 trades | $102,785.00 | $11,730.50 |
| Net without top 10 trades | $62,305.00 | $5,901.50 |
| Top 5 day share | 35.8% | 54.8% |

## Bootstrap Planning Distribution

| Metric | NQ execution | MNQ execution |
|---|---:|---:|
| Total net 95% interval | $45,923-$277,450 | $6,435-$51,440 |
| Annualized net 95% interval | $8,466-$51,147 | $1,186-$9,483 |
| Median annualized net | $28,426 | $4,552 |
| Sharpe 95% interval | 0.41-1.96 | 0.43-1.78 |
| Median max drawdown | -$24,460 | -$3,160 |
| p95 adverse max drawdown | -$42,545 | -$5,609 |
| p99 adverse max drawdown | -$54,603 | -$7,375 |
| P(total net <= 0) | 0.35% | 0.35% |

The bootstrap median drawdown is already worse than the observed path. Capital
planning must use at least p95 adverse drawdown plus operational margin; the
observed drawdown is a historical observation, not a limit.

## Sizing Finding

The old claim that NQ was 18-22% more risk-efficient than MNQ was created by
the invalid `$20 strategy / $2 simulator` MNQ split. Corrected historical
Return/DD is 8.62 for NQ and 9.05 for MNQ. Net divided by p95 bootstrap
drawdown is 3.76 for NQ and 4.62 for MNQ.

This does not prove that a scaled MNQ stack dominates NQ. MNQ's anti-martingale
engages much more often, its top five days supply 54.8% of net, and its maximum
underwater period is 532 sessions. It does establish that MNQ-first validation
is the conservative operational path and that the old recommendation to prefer
NQ on efficiency grounds is withdrawn.

One MNQ is not a `$5,000/month` system: bootstrap median annual net is about
`$4,552`. One NQ's bootstrap median is about `$28,426/year`, or `$2,369/month`
when averaged, with p95 adverse drawdown around `$42,545`. The user's income
goal is therefore a later scaling/capital question, not a current edge test.

## Prop-Account EV

No current prop-account EV is published. The old `$760/month` figure cannot be
reproduced from committed rules. A new calculation requires a versioned account
policy containing profit target, trailing/static drawdown, daily loss rule,
consistency rule, fees, payout split, reset cost, payout timing, and forced
liquidation behavior. Until that exists, quoting a prop EV would manufacture
precision from missing inputs.

## Artifacts

Gitignored reproducible runs:

- `runs/phase1-authority-nq-5yr/`
- `runs/phase1-authority-mnq-5yr/`

The reports must be regenerated from a clean Phase 1 commit before they are
treated as final provenance artifacts.

