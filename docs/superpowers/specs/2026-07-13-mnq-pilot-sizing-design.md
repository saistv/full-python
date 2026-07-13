# MNQ-First Pilot Sizing Design

## Candidate

The operational pilot is flat one-contract MNQ:

- Adaptive Trend signal and exit parameters remain frozen;
- anti-martingale is disabled because the live adapter currently rejects
  multi-contract orders;
- strategy and simulation daily-loss limits are both `$150`;
- the whole pilot stops at `-$500` cumulative P&L;
- evaluation horizon is 30 trading sessions;
- reference costs are 0.75 points per side plus `$1` round-trip commission;
- stress costs are 1.5 points per side plus `$1` round-trip commission.

The `$150` daily limit is simulated, not merely overlaid on a run using the
old `$1,000` NQ limit. It evaluates realized plus gross unrealized P&L at bar
close, flattens at the next open, and halts entries for the session.

## Registered Gate

Reference pilot suitability requires:

1. positive five-year net P&L;
2. at least four of seven anchored forward folds positive;
3. no more than 5% of 30-session moving-block bootstrap paths touch `-$500`
   cumulative P&L from pilot start;
4. p95 adverse 30-session drawdown no worse than `-$500`;
5. positive five-year net P&L under the 1.5-point stress.

The `$5,000` monthly income objective is reported as the probability that a
21-session path ends at or above `$5,000`. It is not a promotion criterion and
cannot be used to increase size during the pilot.

## Outputs

For each cost scenario, report survivability, daily metrics, anchored folds,
full-history block bootstrap, and 30-session pilot-path risk. A separate
21-session path report measures income-target feasibility.

