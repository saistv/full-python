# Opening Auction Regime v1 — T1 Verdict

Filed after executing the frozen T1 design in
`2026-07-17-opening-auction-regime-v1-hypothesis.md`.

## Status

**Rejected on historical train. Robustness trials and historical confirmation
were not evaluated. Do not port v1 to Pine.**

Experiment ID: `oar-v1-20260717`

The experiment registry is closed after one trial with status
`rejected_primary`. The preregistration forbids threshold salvage when T1 fails,
so T2-T11 were not run.

## Data And Execution

- Input authority: `runs/multi-year/nq1_2021-03-16_2026-06-26.csv`
- Bars actually fed: 1,346,662
- First bar: 2021-03-16 00:00 UTC
- Last bar: 2024-12-31 21:59 UTC
- Excluded-session boundary: 2024-12-31 23:00 UTC, the 18:00 ET start of
  CME session 2025-01-01
- Classified sessions: 981
- Gate-eligible sessions: 945
- Warmup/data-ineligible sessions: 36 (feature warmup, 15 roll transitions,
  and one incomplete overnight session)
- Execution: one NQ, $20/point, $10 round trip, 0.75-point entry and exit
  slippage, no open surcharge, next-bar-open fills
- Historical evidence is selection-contaminated at the project level; this was
  development evidence, not a pristine holdout.

Registered provenance:

- data hash: `2c97b6767e8cc27945b4a4f26057d928ad43935f58932d8a1cdbfa8e0fa3acf6`
- train-sequence hash: `a4b3996ca5648d05bf73cc66d49a8cb8d8b983105b8a01c0dc6dc4b7a26ccceb`
- strategy hash: `0045350c103eab1c9a50fb648d2413ce4627b55871f0a8362bec47b16d7ab625`
- simulation hash: `241b25573bdff255f1732359d955d7a2e7f0220d6b907b29a2a1d8b3e4fc04d8`
- executed source-tree hash:
  `279987f9b10f3a09c286c83d638bbb78b2ac971363b27790bb0f118f3378b60a`
- hypothesis hash: `d192af555497b8028292727d5531a28106b12ad0c07080d966b8910aac41c128`

The source tree was uncommitted but content-hashed in full. That is auditable,
but it is another reason this result is research evidence rather than a
production release.

## T1 Results

| Metric | Result | Frozen gate |
|---|---:|---:|
| Trades | 18 | at least 100 |
| Net P&L | $705 | at least $20,000 |
| Profit factor | 1.080 | at least 1.25 |
| Win rate | 27.8% | descriptive |
| Expectancy/trade | $39.17 | positive |
| Session t-stat | 0.140 | at least 2.0 |
| Max drawdown | -$6,945 | descriptive |
| Bootstrap P(total net <= 0) | 59.3% | no more than 5% |
| Bootstrap p95-adverse max DD | -$9,395 | descriptive |
| Net / p95 max DD | 0.075 | at least 1.5 |
| P&L without best trade | -$1,700 | positive |
| P&L without top 3 | -$5,780 | positive |
| P&L without top 5 | -$8,835 | positive |

The 95% bootstrap interval for total train net was approximately
`-$9,301 to +$8,061`; its median was `-$1,228`. The observed +$705 is therefore
noise-compatible, not evidence of a durable edge.

## Branch And Side Diagnosis

### Classifier funnel

| State | Classified | Filled trades |
|---|---:|---:|
| Initiative long | 24 | 11 |
| Initiative short | 21 | 7 |
| Failed auction long | 7 | 0 |
| Failed auction short | 4 | 0 |
| No trade | 889 | 0 |

Of the 45 initiative sessions, 21 armed, 18 confirmed and filled, and 27 ended
through midpoint loss or entry-window expiry. All 11 failed-auction states were
rejected by the frozen structural risk bound; their decision risk was
0.322-0.508 DTR against a maximum of 0.30 DTR.

This is a mechanism result, not merely a low-frequency complaint:

- the strong failed-auction sweep plus sweep-extreme stop produced no executable
  v1 trades under its own risk contract;
- initiative continuation fired only 18 times in 945 eligible sessions;
- the combined result depended on a few winners and was statistically empty.

### Direction split

| Side | Trades | Net | PF | Win rate |
|---|---:|---:|---:|---:|
| Long | 11 | -$5,825 | 0.175 | 9.1% |
| Short | 7 | +$6,530 | 4.679 | 57.1% |

The seven short trades are hypothesis-generating only. Selecting shorts and
discarding longs after seeing this table would violate the frozen symmetric
design and would be extreme small-sample overfitting.

### Calendar cohorts

| Cohort | Trades | Net | PF |
|---|---:|---:|---:|
| 2021 partial | 4 | +$460 | 1.338 |
| 2022 | 5 | -$2,280 | 0.351 |
| 2023 | 4 | -$2,525 | 0.000 |
| 2024 | 5 | +$5,050 | 4.519 |

Only two of four cohorts were positive, and the apparent result was dominated
by five 2024 trades.

## Execution Diagnostics

- Mean fill-relative initial risk: 34.43 points
- Median fill-relative target: 2.94 R
- Mean realized net R: -0.048
- Median realized net R: -1.033
- Modeled commission drag: $180
- Modeled slippage drag: $540
- Total modeled cost drag: $720
- Target-behind-fill occurrences: 0
- Maximum adverse entry gap: 2.0 points

The result is not explained by a target-behind-fill artifact or pathological
entry gap. The entry mechanism itself did not provide sufficient expectancy or
frequency.

## Frozen Gate Decision

T1 passed only the bare positive-expectancy check. It failed trade count,
branch count, net, PF, session significance, bootstrap probability, drawdown
ratio, branch/side balance, outlier dependence, calendar stability, and
top-day dependence.

**Decision: reject Opening Auction Regime v1 without threshold salvage.**

- Do not run T2-T11.
- Do not inspect the 2025-01-01 through 2026-06-26 confirmation window for v1.
- Do not port v1 to Pine or expose it as a user-facing strategy preset.
- Preserve the implementation and artifacts as a falsified research record.

## What A Legitimate v2 Would Need To Change

A v2 cannot be “v1 with a wider risk cap,” a lower volume threshold, shorts
only, or looser initiative filters. Those would be direct reactions to this
result.

A new prefiled mechanism would need a different causal entry geometry. One
plausible direction is to use the opening classification only as context, then
wait for a post-classification retest that creates a new local structural stop
rather than anchoring failed-auction risk at the original sweep extreme. That
would be a new experiment with a new trial family and must be judged from its
own train/prospective evidence.

## Artifacts

- `runs/opening-auction-regime-v1/train-t1/report.json`
- `runs/opening-auction-regime-v1/train-t1/events.jsonl`
- `runs/opening-auction-regime-v1/train-t1/trades.csv`
- `runs/opening-auction-regime-v1/train-t1/auction_sessions.csv`
- `runs/opening-auction-regime-v1/train-t1/auction_diagnostics.csv`
- `runs/opening-auction-regime-v1/experiments.sqlite`
