# Overnight Displacement Reversal v3 — Frozen T1 Verdict

**Run date:** 2026-07-18  
**Experiment:** `overnight-displacement-reversal-v3-20260718`  
**Trial:** T1 of a maximum nine  
**Decision:** `reject_v3_without_threshold_side_or_date_slice_rescue`  
**Registry status:** `completed`

## Verdict

Reject v3. The normal-cost primary trial produced a small positive dollar result,
but failed the sample-size, profit-factor, daily-Sharpe, weekly-R, bootstrap,
drawdown-efficiency, half-year-stability, both-side, and concentration gates. The
result is not sufficiently large, stable, or statistically credible to classify
as an automated trading edge.

T2-T9 are forbidden because their only preregistered purpose was to test
robustness after a complete T1 primary pass. No gap threshold, breadth threshold,
side, stop, target, timing rule, or date slice may replace T1. V3 must not be
ported to Pine, shadow traded, or deployed.

The sealed report also recorded seven reconciliation violations. Post-run
forensic inspection identified all seven as evaluator false positives rather
than strategy-ledger divergences:

- Three correctly paired 11:59 ET time-exit decisions and 12:00 ET next-open
  fills were flagged because the evaluator's reverse lookup matched only symbol
  and reason across the entire sample, then incorrectly required exactly one
  global decision.
- Four zero-gap sessions correctly had no direction and a null direction-aligned
  breadth. The evaluator nevertheless required a finite aligned breadth after it
  had independently derived that the expected direction was null.

These defects do not rescue v3. Even if the reconciliation gate is ignored, the
sealed result still fails twelve other mandatory checks. The immutable report and
registry are not recalculated or amended after inspection.

## Frozen identities

- Git base: `3131121cbe88030a00ed0b097242dcd0541db0f0`
- Executed source tree SHA-256:
  `1c50ad9e60fa52863c68f5b90c5690694356f1b7429361cc2bdfa972ece2e8dd`
- Hypothesis SHA-256:
  `6c71a3bb81e0f329685df4bdfb92ccc572cebf85960e948bd013c8918b7a8a2e`
- Worthiness standard SHA-256:
  `901ecf6c36e7c23b123e8c535fff87a24692c87f51589fbc220946c063a8654c`
- Evaluation-policy SHA-256:
  `40c3da4ace7361e3684fb3e6b8e051473a73eed75126529eabeb913d58551a26`
- Registered train-data SHA-256:
  `214d78175db983ad8b916670242d5740b730f2be008e0db586d4aef00e194ce0`
- Canonical trade SHA-256:
  `bc49ffadc122644979316e99cb4e1ccd46d1c50860e60b1aa5b59ea13286709c`
- Canonical ledger SHA-256:
  `dfbad841304d4bbd529765656eaf3831e761fe8fdca8727167a058d904fbc693`
- Report SHA-256:
  `60c1eb028bcfa76bc272568df6a200512bb40f7bd5cc6e9bec403dc67824598b`

The executed source tree was recorded as dirty because the research build was not
committed. The source, data, specification, artifact, and canonical-replay hashes
identify the exact bytes and result that were scored.

## Score window and integrity

- Input bars constructed: 1,346,662.
- Last constructed bar: `2024-12-31T21:59:00Z`.
- First excluded CME-session boundary: `2024-12-31T23:00:00Z`.
- Expected sessions from the requested start: 981.
- Causal warmup: 21 sessions; first scored session `2021-04-15`.
- Scored sessions: 960, including every no-trade and fail-closed day.
- Missing expected sessions: 0.
- Unexpected closed-session snapshots: 0.
- Active RTH minute-gap sessions: 0.
- Deterministic replay mismatches: 0.
- Entry bracket invalidations at fill: 0.

The event, trade, session, diagnostic, hypothesis, standard, and report file
hashes were rechecked after the run and match the registered hashes. The registry
contains exactly T1 with status `rejected_primary`.

## Primary result

| Measure | T1 result | Required | Pass |
|---|---:|---:|:---:|
| Trades | 155 | >= 300 | No |
| Net P&L | +$3,865 | > $0 | Yes |
| Expectancy per trade | +$24.94 | > $0 | Yes |
| Profit factor | 1.060 | >= 1.25 | No |
| Win rate | 36.77% | Diagnostic | — |
| Median trade | -$460 | Diagnostic | — |
| Observed max drawdown | $9,225 | Diagnostic | — |
| Daily Sharpe | 0.165 | >= 1.25 | No |
| Average calendar-week P&L | +$19.82 | > $0 | Yes |
| Average calendar-week net R | -0.0165R | >= 0.50R | No |
| Bootstrap `P(total net <= 0)` | 37.885% | < 5% | No |
| Annualized net / adverse p95 drawdown | 0.0458 | >= 1.00 | No |
| Positive complete half-years | 4 of 7 (57.1%) | >= 70% | No |
| Final complete half-year | +$25 | > $0 | Yes |
| Net without top five trades/days | -$7,780 | > $0 | No |
| Top-five-day share of net | 301.3% | <= 35% | No |

The 20,000-draw, 10-session block bootstrap estimated adverse p95 drawdown of
$22,135.25 and adverse p99 drawdown of $27,975.05. The probability of a
nonpositive total was 37.885%, roughly one chance in 2.64, rather than the
required less than one chance in 20.

Modeled normal costs were $6,200: $1,550 commission and $4,650 slippage. The
positive $3,865 net is therefore not ignored, but PF 1.060 leaves little margin
for model error, and the median trade and average realized net R were both
negative. The IID probability statistic was only 62.8% and is diagnostic rather
than a gate because it does not correct for serial dependence.

The capital-policy gate was not evaluated because no allocated-capital and hard
loss-limit pair was supplied. That pending input cannot change the rejection
because the core edge gates already failed.

## Side and regime stability

- Long: 69 trades, -$1,945, PF 0.936, -$28.19 expectancy per trade.
- Short: 86 trades, +$5,810, PF 1.172, +$67.56 expectancy per trade.
- Complete half-years: four positive and three negative.
- Final fold, 2024-H2: +$25, PF 1.003.
- Maximum losing streak: 10 trades.

The short book is the only positive side, but it still misses PF 1.25 and was
defined as one half of a mirrored mechanism. Selecting it after T1 would be a
forbidden side rescue. The final fold's $25 profit is economically flat and does
not offset the broader instability.

## Mechanism funnel

The mechanism generated enough activity to evaluate its basic premise but not the
required sample or quality:

- 960 scored sessions;
- 761 gap-size-eligible sessions;
- 712 sessions with aligned overnight displacement;
- 565 fully eligible non-roll classifications;
- 434 extensions armed;
- 155 confirmations, intents, fills, and closed trades;
- 158 signals rejected by frozen risk/reward geometry; and
- 252 sessions cancelled before entry.

Loosening the geometry or breadth rules after seeing T1 would change the tested
hypothesis. More importantly, frequency is not the only problem: the trades that
did qualify were concentrated and weak after costs.

## Final action

- Do not run T2-T9.
- Do not alter or rerun v3 T1.
- Do not port v3 to Pine or deploy it in shadow/live execution.
- Preserve the report, registry, ledger, trades, snapshots, diagnostics, and
  hashes as the immutable record.
- Correct the two evaluator rules only prospectively, with regression tests,
  before evaluating any future strategy; do not use the correction to rescore v3.
- Any future candidate must be a new economic hypothesis and version with a new
  preregistration. It may not be described as a v3 threshold or side adjustment.

The 2025-and-later confirmation period remains physically excluded from this
historical run. It must remain sealed until a genuinely new candidate clears its
historical and preregistered robustness gates.
