# Opening Auction Regime v1 — Preregistered Hypothesis

## Status

**Preregistered before the first strategy replay.**

Experiment ID: `oar-v1-20260717`

This document freezes the default mechanism, historical windows, trial budget,
and rejection gates. The default may be rejected, but it may not be replaced by
a better-looking neighboring cell after results are known.

No historical period in this repository is described as genuinely untouched.
The project has already inspected aggregate outcomes across the available
2021-2026 history. Historical confirmation is therefore selection-contaminated;
the final validation gate is a fixed prospective shadow window.

## Objective

Test whether the first 15 completed RTH minutes identify one of two auction
states with positive post-cost expectancy:

1. **Initiative acceptance:** price leaves overnight inventory with directional
   efficiency, participation, and repeated acceptance beyond running VWAP.
2. **Failed auction:** price sweeps an external overnight/prior-day level, then
   reclaims it with participation, strong close location, and repeated VWAP
   acceptance in the opposite direction.

Every session that does not uniquely satisfy one state is `NO_TRADE`.

This is not the rejected generic VWAP fade or opening-range fade:

- no distance-from-VWAP entry;
- no daily-ADX permission;
- no post-10:00 failure of a 30-minute opening range;
- no one-minute ATR extremity;
- no entry merely because price returns inside a range.

The continuation sleeve still has continuation economics. It must not be
marketed as unrelated to momentum; the different thesis is the causal auction
state, external-level acceptance, and pullback/re-acceleration entry.

## Causal Feature Set

All classification inputs are frozen after processing the bar timestamped
09:44 ET. Bar timestamps denote bar open, so this observes exactly
`[09:30, 09:45)` and permits a next-bar-open fill at 09:45 for a failed auction.

- `DTR20`: median true range of the prior 20 completed RTH sessions. Each true
  range uses that session's RTH high/low and the preceding RTH close.
- `V15ratio`: current 09:30-09:44 volume divided by the median first-15-minute
  volume of the prior 20 complete open sessions.
- `OR15`: 09:30 open, high/low through 09:44, and 09:44 close. `W` is width and
  `M` is midpoint.
- `ER`: `abs(C-O)` divided by the path length from the RTH open through all 15
  opening closes.
- `CLV`: `(C-L)/W`.
- Running RTH VWAP: volume-weighted typical price, calculated sequentially.
- VWAP acceptance: count of opening closes on the directional side of their
  contemporaneous VWAP.
- External levels: overnight high/low and prior RTH high/low/close, all frozen
  before the current RTH open.

Missing references, incomplete opening minutes, zero width/volume, or a
continuous-contract roll transition fail closed to `NO_TRADE`. Roll exclusion
is a data-geometry safeguard: prior-contract price levels are not comparable to
the new contract's opening auction.

Overnight coverage is also a data-quality permission, not an alpha filter. The
first observed overnight bar must be within five minutes of the 18:00 ET
session start, the last within five minutes of 09:29 ET, and no observed gap may
exceed 15 minutes. Otherwise the session is `NO_TRADE` with reason
`incomplete_overnight_coverage`. Historical reports must disclose the count;
the prospective runtime additionally remains subject to the feed-outage halt.

## Frozen Classification

### Initiative long

All must hold:

- `C-O >= 0.15 * DTR20`;
- `ER >= 0.55`;
- `CLV >= 0.80`;
- at least 12 of 15 closes above contemporaneous VWAP;
- `C >= overnight_high + 0.05 * DTR20`;
- `V15ratio >= 1.00`.

Initiative short is the exact mirror.

### Failed-low auction — long reversal

All must hold:

- OR low breaches the outermost of overnight low and prior RTH low by at least
  `0.10 * DTR20`;
- the opening close reclaims that level by at least `0.05 * DTR20`;
- `CLV >= 0.65`;
- the last three opening closes are above contemporaneous VWAP;
- `V15ratio >= 1.00`.

Failed-high auction is the exact short mirror. If multiple states qualify, the
session is two-sided/conflicted and becomes `NO_TRADE`.

## Frozen Entry And Exit Rules

- Maximum one entry attempt and one filled trade per session.
- Flat quantity: one NQ for edge research. No anti-martingale, daily-loss signal
  veto, re-entry, scaling, breakeven, trailing stop, or partial exit.
- **Initiative:** from 09:45 through 10:14, wait for a pullback of at least 25%
  of `W` from the OR extreme while the close remains beyond both `M` and VWAP.
  Arm once. On a later bar, confirm when the close exceeds the prior bar's high
  for a long (low for a short) and remains beyond VWAP. A close through `M`
  cancels the session; touching `M` also cancels (`long close <= M`, short close
  `>= M`).
- **Failed auction:** emit at the 09:44 decision close and fill at the next bar
  open when execution permits.
- **Initiative stop:** beyond the observed pullback extreme by
  `0.05 * DTR20`.
- **Failed-auction stop:** beyond the OR sweep extreme by `0.05 * DTR20`.
- Reject decision-close risk outside `[0.08, 0.30] * DTR20`.
- Initiative target: 3.0 decision-close R.
- Failed-auction target: prior RTH close, capped at 3.0 decision-close R.
  Reject if the prior close is not in the trade direction or offers less than
  1.5 decision-close R.
- Stops and targets are frozen at the decision close. Reports must separately
  disclose actual fill-relative R and any target-behind-fill occurrence.
- Signal the time exit on the 11:29 bar for a normal next-bar-open exit at
  11:30 ET. The exchange-calendar flatten remains the final backstop.

Protective stops round away from the market to the NQ tick; targets round toward
the market. This avoids Python banker-rounding ambiguity.

## Data And Execution Contract

- Historical development/train: session dates from 2021-03-16 through
  2024-12-31. The first 20 valid sessions are feature warmup only.
- One-shot historical confirmation: 2025-01-01 through 2026-06-26, only after
  every train gate passes. This window is explicitly selection-contaminated at
  the project level.
- True final holdout: the first 126 open RTH sessions arriving after the
  strategy, config, code, and data hashes are frozen. No optional stopping.
- Default execution: next-bar open, one NQ, $20/point, $10 round-trip
  commission, 0.75 point entry slippage, 0.75 point exit slippage, no separate
  RTH-open surcharge.

The train runner must stop feeding bars before session date 2025-01-01. The
normal all-history CLI is not an authoritative research command for this test.

## Trial Budget

Maximum historical executions: **11**.

1. T1 frozen default.
2. T2-T3 observation length diagnostics: 10 and 20 minutes.
3. T4-T5 initiative displacement: 0.12 and 0.18 DTR.
4. T6-T7 VWAP acceptance: 11/15 and 13/15.
5. T8-T9 opening volume ratio: 0.80 and 1.20.
6. T10-T11 default at 1.00 and 1.25 points slippage per side.

T2-T11 run only if T1 passes its primary gates. They are robustness diagnostics;
none may replace the default. If T1 fails, v1 closes without threshold salvage.
For the 10/20-minute observation diagnostics, initiative VWAP acceptance stays
fixed at 80% of observed bars (8/10 and 16/20); every other threshold is
unchanged.

## Train Promotion Gates

All are required before historical confirmation:

- deterministic provenance hashes and replay;
- at least 100 trades total and at least 50 in each active branch;
- net P&L at least $20,000, profit factor at least 1.25, positive expectancy,
  and session-level t-statistic at least 2.0;
- 10-session block-bootstrap probability of nonpositive total net no more than
  5%, and net / absolute p95-adverse max drawdown at least 1.5;
- continuation and failed-auction branches each positive with PF at least 1.0;
- long and short sides each positive; neither may be deleted after inspection;
- positive net after removing the top 1/3/5 trades and top 1/3/5 days, with
  top-five-day share no more than 40%;
- at least three of partial-2021, 2022, 2023, and 2024 positive;
- default PF at least 1.10 at 1.00 point slippage per side and positive at 1.25;
- across T2-T9, median PF at least 1.10, at least six of eight cells positive,
  and no cell below PF 0.90.

## Historical Confirmation Gates

- positive net and expectancy, PF at least 1.10, and at least 50 trades;
- both branches and both sides nonnegative;
- at least two of 2025-H1, 2025-H2, and 2026-H1 positive;
- net after top three trades and days remains positive;
- positive under 1.00 point slippage per side.

Any failure closes v1. No alternate cell receives another confirmation attempt.

## Prospective Gate

The frozen default must run for exactly 126 open RTH shadow/paper sessions and
produce at least 50 trades, positive net after observed costs, PF at least 1.10,
no branch or side loss large enough to erase combined net, exact decision/fill
reconciliation, and zero risk-state violations. Insufficient trade count is an
insufficient-evidence verdict, not permission to extend the window.

Passing allows consideration of a limited MNQ pilot. It is not a profitability
guarantee.

## Mandatory Diagnostics

- session funnel: eligible, classified, armed, confirmed, rejected, filled;
- classifier and order rejection reasons;
- branch, side, year, half-year, reference type, and exit reason;
- DTR, `V15ratio`, OR-width/DTR, gap/DTR, entry gap, and cost drag;
- fill-relative R, MFE/MAE, and target-behind-fill count;
- top trade/day dependence, roll/abbreviated-session attribution;
- outcome of classified but untraded sessions;
- fire-day overlap and daily P&L correlation with Adaptive Trend when available;
- explicit leakage audit.

Prior ADX and variance ratio may be reported descriptively only. They cannot
become gates inside this experiment.
