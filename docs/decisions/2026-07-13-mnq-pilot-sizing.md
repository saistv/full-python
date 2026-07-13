# MNQ-First Pilot Sizing Gate

**Decision:** reject a 30-session funded pilot under the `$500` cumulative
loss budget. Retain flat one MNQ as the eventual live pilot instrument, but use
the `$500` budget for at most a 10-session operational pilot after all observe,
demo-order, paper, and reconciliation gates pass. This is not authorization to
enable orders.

## Registered Candidate

- Flat 1 MNQ; anti-martingale disabled
- Frozen Adaptive Trend signals, stops, and exits
- `$150` strategy and simulation daily-loss limit
- `$500` cumulative pilot loss stop
- 30-session validation horizon
- Reference: 0.75 points slippage per side plus `$1` commission
- Stress: 1.5 points slippage per side plus `$1` commission
- Clean authority source: `631905a`

This differs deliberately from the corrected `$25,931.50` MNQ authority run,
which allows anti-martingale sizing up to four contracts and uses a `$1,000`
daily cap. The live adapter currently rejects multi-contract submission, so
that result is not a valid pilot forecast.

## Five-Year Results

| Scenario | Trades | Net P&L | PF | Max DD | Positive folds | Annualized bootstrap median |
|---|---:|---:|---:|---:|---:|---:|
| Reference | 859 | $12,554.50 | 1.326 | -$2,036 | 4/7 | $2,203.76 |
| Stress | 859 | $9,977.50 | 1.246 | -$2,534 | 3/7 | $1,730.73 |

The flat-one-MNQ edge survives doubled slippage, but remains regime-dependent.
Both 2023 halves lose at reference costs; stress costs also make 2024 H1
slightly negative.

Full-history bootstrap p95/p99 drawdown is approximately `-$4,411/-$5,761`
at reference costs and `-$5,411/-$6,839` under stress. A future indefinite
one-MNQ deployment therefore needs much more risk capital than the short pilot
budget. The pilot stop is an experiment boundary, not a claim that `$500`
covers the strategy's long-run drawdown.

## Thirty-Session Gate

| Measure | Reference | Stress |
|---|---:|---:|
| Median ending P&L | $158.00 | $102.00 |
| 95% ending interval | -$871.54 to $2,048.07 | -$933.04 to $1,988.11 |
| Probability ending positive | 59.4% | 56.2% |
| Probability touching -$500 | 23.2% | 26.8% |
| p95 minimum equity from start | -$818.00 | -$872.03 |
| p95 drawdown from peak | -$901.00 | -$950.03 |
| p99 drawdown from peak | -$1,111.01 | -$1,168.01 |

The registered gate required no more than 5% probability of exhausting the
budget and p95 drawdown within `$500`. Both rows fail. To cover roughly 95% of
30-session paths would require about `$900` starting-loss capacity and a
`$1,000` drawdown reserve. A p99 envelope is about `$1,200`. Those are measured
risk requirements, not permission to raise the agreed budget.

## Fixed-Horizon Risk Ladder

Probability that a `-$500` cumulative stop is touched:

| Funded horizon | Reference | Stress | Reference p95 DD | Stress p95 DD |
|---|---:|---:|---:|---:|
| 5 sessions | 0.0% | 0.0% | -$264.00 | -$276.00 |
| 10 sessions | 2.3% | 2.5% | -$439.00 | -$460.50 |
| 15 sessions | 8.6% | 10.1% | -$576.00 | -$606.00 |
| 20 sessions | 14.5% | 17.1% | -$708.50 | -$746.50 |
| 30 sessions | 24.0% | 27.4% | -$915.00 | -$963.53 |

The slightly different 30-session percentages from the registered gate use a
separate deterministic seed for the horizon ladder; both estimates lead to the
same decision.

## Income Objective

Over 21 sessions, median reference P&L is `$84.50`; the 95% upper endpoint is
`$1,687`, and the best observed rolling 21-session result is `$3,471.50`.
Neither 10,000 bootstrap draws nor any historical 21-session window reaches
`$5,000` with flat one MNQ. The `$5,000/month` objective is not plausible at
pilot size. Increasing size to force that objective would violate the risk
gate and the live adapter's one-contract safety boundary.

## Operational Plan

1. Complete the existing demo observe, demo order, paper, and reconciliation
   gates with no funded exposure.
2. If those pass, run at most 10 funded sessions with flat 1 MNQ, `$150` daily
   stop, and `$500` cumulative stop.
3. Judge that funded pilot on order integrity, slippage, latency, position
   reconciliation, and safety behavior, not profitability; ten sessions cannot
   validate the edge.
4. Continue a minimum 30-session paper/shadow record for performance evidence.
5. Do not extend the funded horizon or raise the loss budget automatically.
   A separate decision must compare live execution evidence with the measured
   `$1,000` p95 / `$1,200` p99 30-session envelope.

Artifacts (gitignored):

- `runs/mnq-pilot-sizing-v3.json`
- `runs/mnq-pilot-sizing-v3.sqlite`

