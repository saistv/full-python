# Automated Strategy Worthiness Standard

**Status:** Active house standard  
**Effective date:** 2026-07-17  
**Scope:** Every new automated futures strategy and every material strategy revision

## Purpose

This document is a promotion contract, not a promise of profitability and not an
optimization target. A strategy must be specified before its scored replay. It is
then promoted only when the evidence clears the gates below after realistic costs.
A failed gate cannot be rescued by searching the same sample for better settings;
that creates a new version, consumes another registered trial, and requires a new
hypothesis document.

No fixed weekly dollar target is valid across account sizes or instruments. Weekly
performance is judged in `R` (initial risk), percent of allocated capital, and
dollars together. Unless a strategy specification freezes a stricter definition,
realized net `R` is net trade P&L divided by absolute fill-to-frozen-stop distance,
point value, and quantity.

## Non-negotiable research controls

- Freeze the economic hypothesis, exact entry/exit rules, defaults, data boundary,
  costs, decision gates, and maximum trial budget before the first scored run.
- Use only information available at the decision timestamp. Audit session labels,
  contract rolls, indicators, fills, stops, targets, and exits for look-ahead.
- Score every expected eligible session, including missing/fail-closed and
  zero-trade days as zero. Report daily returns on the same allocated-capital
  denominator used for Sharpe and drawdown.
- Include commissions, bid/ask or slippage, adverse next-bar market fills, exchange
  hours, forced flattening, rejected/missed entries, and contract-roll behavior.
- Keep an append-only experiment registry containing code, parameters, input-data,
  specification, and result hashes. Report the global related-family trial count;
  a new version number does not reset multiplicity.
- Separate development history from untouched confirmation data. Once viewed, a
  period is no longer out-of-sample, even if the candidate did not trade there.
- No sizing progression may manufacture edge. Establish one-contract expectancy
  first; evaluate sizing and portfolio allocation only after strategy promotion.

## Historical evidence gates

Unless a row explicitly names a later stage, every mandatory minimum is required
for promotion from `primary-qualified` to `research-worthy`. Normal-cost primary
passage alone never earns `research-worthy` status.

| Dimension | Mandatory minimum | Strong target |
|---|---:|---:|
| Net trade count | 300 trades across at least 3 years and materially different regimes | 500+ trades across 5+ years |
| Net profit factor | 1.25 | 1.35-1.60 |
| Daily Sharpe ratio | 1.25 | 1.50+ |
| Multiplicity-aware confidence | Not a primary-trial gate. Before `limited-live-worthy`, DSR >= 95% using observed related-trial Sharpe dispersion and a defensible effective independent-trial count; if unavailable, advancement stops at `shadow-worthy` | 97.5%+ |
| Bootstrap loss probability | `P(total net P&L <= 0) < 5%` | < 2.5% |
| Average calendar-week result | >= 0.50R and positive in dollars | 0.75-1.00R |
| Return on allocated capital | Reported; no universal mandatory percentage | 0.25-0.50% average per week |
| Annualized net / adverse bootstrap p95 max drawdown | >= 1.00 | >= 1.50 |
| Six-month stability | >= 70% of complete folds net positive | >= 80% |
| Final chronological historical fold | Net positive | Positive with PF >= 1.10 |
| Top-five-day concentration | Top five days contribute <= 35% of total net; result remains net positive without them | <= 25% |
| Doubled-cost stress | Net positive with profit factor >= 1.10 | Profit factor >= 1.20 |
| Bootstrap p99 drawdown | <= 25% of allocated capital and <= 50% of the predeclared hard loss limit | <= 15% of allocated capital |

Additional integrity requirements:

- Both long and short books must be disclosed. A deliberately one-sided strategy
  is allowed, but a materially losing side cannot be hidden inside the aggregate.
- Each independently enabled branch needs at least 50 historical trades and
  non-negative net expectancy. Otherwise it is diagnostic-only or removed before
  the prospective specification is frozen.
- Report mean and median trade, win/loss asymmetry, trade and daily t-statistics,
  maximum adverse/favorable excursion, holding time, exposure, consecutive losses,
  year/month/weekday/session slices, and score-window coverage.
- The result must remain net positive with profit factor >= 1.10 under at least one
  plausible adverse fill-timing scenario and, separately, under doubled costs.
  Same-bar stop/target ambiguity must use the declared conservative policy.
- IID PSR may be disclosed as a diagnostic, but is not a gate unless serial
  dependence is corrected. Block-bootstrap confidence is the T1 gate. If earlier
  manual or legacy variants cannot be reconstructed credibly, project-global DSR
  is reported unavailable; it must never be calculated from a trial count alone or
  as though the surviving candidate were the first trial.

## Prospective confirmation gates

Historical passage authorizes shadow observation, not live capital. The frozen
candidate must then produce at least 100 prospective trades with:

- net profit factor >= 1.15;
- daily Sharpe >= 1.00;
- positive net P&L after observed costs;
- drawdown within the preregistered limit and no risk-policy breach;
- direction, branch, holding-time, slippage, and trade-frequency behavior broadly
  consistent with the historical confidence ranges; and
- exact reconciliation among signal, order intent, broker acknowledgement,
  side/quantity/state transitions, position, exit, and P&L records. Fill prices
  reconcile within a preregistered tick/time tolerance because market fills cannot
  be expected to equal a model price exactly; every exception is explained.

Prospective gates are conjunctive. Calendar time alone does not replace the minimum
trade sample, and a favorable historical result cannot offset a failed live-shadow
gate.

## Promotion states

1. **Rejected** — hypothesis, implementation, or any mandatory historical gate
   fails. Do not tune the failed version on the same sample.
2. **Primary-qualified** — the frozen normal-cost primary trial passes. Eligible
   only for preregistered neighborhood, fill-timing, and cost-stress trials.
3. **Research-worthy** — every historical, robustness, fill-timing, doubled-cost,
   capital-policy, and implementation-audit gate passes. Eligible only to prepare
   a frozen shadow specification.
4. **Shadow-worthy** — the prospective specification is frozen and automated
   reconciliation and hard risk controls are operational. Eligible to collect
   prospective evidence, not to trade live capital.
5. **Limited-live-worthy** — all prospective gates and DSR pass, broker
   reconciliation is exact, and a predefined small-size pilot has hard
   daily/weekly/total risk caps.
6. **Unattended-worthy** — the limited-live pilot completes without unexplained
   divergence or risk breach. Size remains constrained by bootstrap p99 drawdown,
   liquidity, broker margin, and the account's independent hard loss limit.

Any unexplained execution mismatch, missing data, stale account state, or breach of
a hard risk limit immediately blocks promotion regardless of performance.

## Decision rule for the current build

The first frozen configuration is the primary trial. If it fails a core edge gate
(sample size, profit factor, daily Sharpe, bootstrap loss probability, stability,
or concentration), the version closes as rejected. Neighbor tests are permitted
only after primary passage and may test robustness, not select the best backtest.
Confirmation data remains sealed until the historical candidate and every
prospective rule are frozen.

## Statistical references

- Andrew W. Lo, *The Statistics of Sharpe Ratios*, Financial Analysts Journal,
  2002: https://doi.org/10.2469/faj.v58.n4.2453
- David H. Bailey and Marcos Lopez de Prado, *The Deflated Sharpe Ratio*, 2014:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Campbell R. Harvey, Yan Liu, and Heqing Zhu, *... and the Cross-Section of
  Expected Returns*, 2014: https://www.nber.org/papers/w20592

These papers motivate uncertainty, non-normality, and multiple-testing controls;
the numerical thresholds in this document are conservative project governance
choices, not universal guarantees or claims made by those authors.
