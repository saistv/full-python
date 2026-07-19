# Opening Auction Level-Retest v2 — Frozen T1 Verdict

**Run date:** 2026-07-17  
**Experiment:** `oar-retest-v2-20260717`  
**Trial:** T1 of a maximum nine  
**Decision:** `rejected_primary_no_threshold_rescue`  
**Registry status:** `completed`

## Verdict

Reject v2. The normal-cost primary trial failed the sample-size, expectancy,
profit-factor, Sharpe, weekly-return, bootstrap-confidence, drawdown-efficiency,
fold-stability, branch/side, and concentration gates. T2–T9 are forbidden because
their only preregistered purpose was to test robustness after a T1 primary pass.
No branch, side, threshold, reference type, or date slice may replace T1.

This is an economic-hypothesis failure, not a replay-integrity failure. The train
loader respected the sealed boundary, expected-session coverage was complete, two
independent replays matched exactly, and execution reconciliation found no
violations.

## Frozen identities

- Git base: `3131121cbe88030a00ed0b097242dcd0541db0f0`
- Executed source tree SHA-256:
  `a7909d56ab6ada4c825b8dcec50c8aff9ce1f03055f052d968c46497ba6f63ce`
- Hypothesis SHA-256:
  `71b4e38c0c2ed0563ca711b4a9e13b54160767abde3cb133671afafa27587474`
- Worthiness standard SHA-256:
  `901ecf6c36e7c23b123e8c535fff87a24692c87f51589fbc220946c063a8654c`
- Evaluation-policy SHA-256:
  `5e4db3e93d9f4778ef7206ebcebd940b6ee133db7fe0adc94c43ef6d7b299872`
- Registered train-data SHA-256:
  `2c97b6767e8cc27945b4a4f26057d928ad43935f58932d8a1cdbfa8e0fa3acf6`
- Canonical trade SHA-256:
  `91593b6e782ca0ef4ac389ad017f9a6947db6d261a67712c8992412484e3e007`
- Canonical ledger SHA-256:
  `1cb71f9ef00380408e931877841c7151bbfa179238124efd05152b8040e76ecd`

The executed source tree was intentionally recorded as dirty because the new v1
and v2 research files had not been committed. The content hash, artifact hashes,
and deterministic second replay identify the exact executed bytes and results.

## Score window and integrity

- Input bars constructed: 1,346,662.
- Last constructed bar: `2024-12-31T21:59:00Z`.
- First excluded CME-session boundary: `2024-12-31T23:00:00Z`.
- Requested CME sessions: 981.
- Causal warmup: 21 sessions; first scored session `2021-04-15`.
- Scored sessions: 960, including every zero-trade and fail-closed day.
- Missing expected sessions: 0.
- Unexpected closed-session snapshots: 0.
- Feature-ready sessions: 945; fail-closed roll sessions: 15.
- Deterministic replay mismatches: 0.
- Signal/order/fill/trade/P&L reconciliation violations: 0.
- Fill-time bracket invalidations or targets behind fills: 0.

## Primary result

| Measure | T1 result | Required | Pass |
|---|---:|---:|:---:|
| Trades | 11 | >= 300 | No |
| Net P&L | -$1,655 | > $0 | No |
| Expectancy per trade | -$150.45 | > $0 | No |
| Profit factor | 0.636 | >= 1.25 | No |
| Win rate | 36.36% | Diagnostic | — |
| Median trade | -$540 | Diagnostic | — |
| Daily Sharpe | -0.289 | >= 1.25 | No |
| Average calendar-week net R | -0.0157R | >= 0.50R | No |
| Bootstrap `P(total net <= 0)` | 71.45% | < 5% | No |
| Observed annualized net / p95 drawdown | -0.061 | >= 1.00 | No |
| Positive complete half-years | 2 of 7 | >= 5 of 7 | No |
| Final half-year net | -$1,475 | > $0 | No |
| Net without top five trades | -$4,545 | > $0 | No |

Observed maximum drawdown was $3,285. The 20,000-draw, 10-session block bootstrap
estimated adverse p95 drawdown of $7,120 and adverse p99 drawdown of $8,880.10.
The p99 value is disclosed, but the capital-policy gate was not evaluated because
no strategy-capital and hard-loss-limit pair was supplied. That pending gate cannot
change the rejection because the core edge gates already failed.

## Mechanism funnel

The mechanism was both too sparse and negative after costs:

- 960 scored sessions;
- 332 sessions classified as accepted/rejected external-level context;
- 317 cancelled before entry;
- 35 first retests armed;
- 11 confirmed and filled;
- 4 confirmations rejected for risk geometry; and
- 11 closed trades, or 1.15% of scored sessions.

The strict first-contact hold and later-confirmation requirements explain the low
frequency, but loosening them after seeing T1 would be a threshold rescue. The
small sample is itself a mandatory failure, and the trades that survived were not
profitable as a group.

## Diagnostic slices are not rescue candidates

- `accepted_break`: 4 trades, -$1,775, PF 0.008.
- `rejected_break`: 7 trades, +$120, PF 1.044.
- Long: 5 trades, -$2,575, PF 0.006.
- Short: 6 trades, +$920, PF 1.471.
- Overnight-high reference: 6 trades, +$1,505, PF 2.087.

The positive short/reference slices contain only six trades and are selected after
inspection. Promoting them would violate the frozen both-branch/both-side rule and
would convert noise into an apparent strategy.

## Final action

- Do not run T2–T9.
- Do not port v2 to Pine or deploy it in shadow/live execution.
- Preserve the report, registry, ledger, trades, session snapshots, diagnostics,
  and hashes as the immutable record.
- Any subsequent candidate must be a new economic hypothesis and version, with a
  new preregistration and trial budget. It may not be described as a v2 threshold
  adjustment or use the positive post-hoc slices above as its entry definition.

Historical data used here was already selection-contaminated by earlier project
work. Even a T1 pass would only have been development evidence; this rejection is
therefore final for v2 without consuming any prospective data.
