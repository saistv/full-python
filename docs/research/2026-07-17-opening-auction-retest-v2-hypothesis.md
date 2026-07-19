# Opening Auction Level-Retest v2 — Preregistered Hypothesis

**Frozen before scored replay:** 2026-07-17  
**Candidate:** `opening_auction_retest_v2`  
**Primary trial:** T1  
**Requested historical window:** 2021-03-16 through 2024-12-31  
**Promotion authority:** `docs/specs/2026-07-17-automation-worthiness-standard.md`

## Thesis

Known overnight and prior-RTH extremes attract liquidity. A break has evidence
only when value remains beyond the crossed level; a rejection has evidence only
when value returns inside. Neither classification is an entry. The hypothesized
edge is that the **first** controlled post-opening retest of that level provides a
falsifiable structural stop and a sufficiently asymmetric payoff.

This is not an iteration of the Adaptive Trend momentum stack. Opening
displacement, efficiency, volume, close location, gap, ADX, RSI, moving averages,
and trend alignment are diagnostics only. There is no live/backtest mode, branch
switch, failsafe menu, broker-confirmation setting, sizing progression, re-entry,
or discretionary override.

The two inseparable branches are:

- `accepted_break`: continuation after value accepts beyond an external level;
- `rejected_break`: reversal after a sweep fails and value returns inside.

Both branches and both directions remain enabled for T1. A losing branch or side
may not be deleted after inspection.

## Information frozen at 09:44 ET

Use only completed information available at the close of the 09:44 one-minute bar:

- all 09:30–09:44 bars and running RTH VWAP;
- median true range of the most recent 20 completed non-roll RTH sessions
  (`DTR20`);
- overnight high/low and prior RTH high/low;
- prior RTH close, opening volume ratio, displacement, efficiency, and close
  location for diagnostics only; and
- continuous-contract roll and overnight-coverage state.

Fail closed on a missing opening minute, incomplete overnight coverage, roll
transition, missing reference history, non-finite values, or non-positive DTR20.
The classification and its reference never change after 09:44.

Complete overnight coverage requires at least two bars, a first bar no later than
18:05 ET, a final bar no earlier than 09:24 ET, and no consecutive overnight
timestamp gap greater than 15 minutes.

## External reference selection

Only a level that began beyond the 09:30 RTH open and was actually crossed during
the opening observation is eligible.

High-side reference candidates are overnight high and prior RTH high where:

```text
level > RTH open
opening high >= level + 0.02 × DTR20
```

Choose the highest crossed high. Low-side rules mirror this and choose the lowest
crossed low:

```text
level < RTH open
opening low <= level - 0.02 × DTR20
```

Equal overnight/prior-RTH prices receive a combined audit label. The algorithm has
no nearest-level or clustering parameter.

## Classification

Let `C` be the 09:44 close.

Accepted high break (`accepted_break`, long):

- a high reference exists;
- `C >= reference + 0.01 × DTR20`;
- at least four of the final five opening closes exceed the reference; and
- final opening VWAP exceeds the reference.

Rejected high break (`rejected_break`, short):

- a high reference exists;
- `C <= reference - 0.01 × DTR20`;
- all final three opening closes are below the reference; and
- final opening VWAP is below the reference.

Accepted low break (short) and rejected low break (long) are exact mirrors. Exactly
one candidate must qualify. Multiple candidates, even when they point in the same
direction, produce `no_trade:conflicting_auction_evidence`.

## First-retest state machine

```text
observe → classify → wait_first_retest → armed → entry_pending → position → done
```

Any invalidation goes directly to `done`. There is at most one attempt and one
filled trade per session.

Search for the first contact from 09:45 through 10:28. For a long, contact occurs
when `bar low <= reference + 0.03 × DTR20`. It is valid only when:

- the bar has positive range;
- close is at least `reference + 0.01 × DTR20`; and
- `(close - low) / (high - low) >= 0.65`.

Short rules mirror exactly. The first contact is decisive: an invalid first contact
cancels the session rather than searching for a cleaner later chart pattern.

A valid retest arms its high for a long or low for a short. One of the next three
completed bars, and no later than 10:29, must confirm. Long confirmation requires:

- close at least one tick above the retest-bar high;
- close at least `reference + 0.01 × DTR20`; and
- close above contemporaneous running RTH VWAP.

Short confirmation mirrors exactly. While armed, a long cancels on a close below
`reference - 0.01 × DTR20`; a short cancels above the mirrored boundary. Expiry of
three bars or the 10:30 entry boundary also cancels. The most adverse extreme from
retest through confirmation defines the local structure.

## Risk and exits

- Research size: one NQ contract, flat sizing.
- Fill model: next-bar open.
- Long stop: lowest retest-to-confirmation low minus `0.02 × DTR20`; short mirrors.
- Decision-close risk must be within `[0.04, 0.16] × DTR20`.
- Static target: `2.5R` from decision close and structural stop.
- Signal time exit at 11:29 for an expected 11:30 next-open fill.
- Stops round away from the market; targets round toward it.
- No trailing, breakeven, partial exit, scaling, re-entry, martingale, or
  signal-level daily-loss veto.
- Baseline costs: $10 round trip plus 0.75 NQ point adverse slippage per side.
- A bar touching both stop and target resolves stop first.

Stop and target are frozen before the next-bar fill. Reports must separately show
actual fill-relative risk/reward, adverse entry gaps, targets behind the fill,
realized net R, and any fill-time bracket invalidation. Realized net `R` is net
trade P&L divided by absolute fill-to-frozen-stop distance, NQ point value, and
quantity.

## Trial protocol

The runner must physically stop supplying bars before the CME session beginning
2025-01-01. The reused history is selection-contaminated by earlier project work;
it is development evidence, never untouched confirmation.

Maximum candidate-family budget: nine trials.

- T1: frozen defaults above.
- T2/T3: acceptance closes required = 3/5 and 5/5.
- T4/T5: retest contact zone = 0.02 and 0.04 DTR.
- T6/T7: confirmation lifetime = 2 and 4 bars.
- T8: one additional completed one-minute bar of entry latency
  (`entry_delay_bars=1`).
- T9: $20 round trip and 1.50 points adverse slippage per side.

T2–T9 are forbidden unless T1 first clears every normal-cost primary gate. They
test robustness and stress; none may replace T1. T2–T7 pass as a neighborhood only
if every trial remains net positive, their median profit factor is at least 1.15,
at least four of six have profit factor at least 1.10, and no trial retains fewer
than half of T1's trades. T8 is the mandatory adverse-fill-timing stress and T9 the
mandatory doubled-cost stress; each must remain net positive with profit factor at
least 1.10. Candidate-family DSR is computed only after actual cross-trial Sharpe
dispersion exists; the declared budget alone is not a DSR input. Project-global
DSR remains unavailable if effective historical multiplicity and trial results
cannot be reconstructed defensibly. DSR is not a T1 gate, but DSR below 95% or
unavailable blocks advancement beyond `shadow-worthy`.

## T1 normal-cost primary gates

All checks are conjunctive:

- deterministic code/config/data/specification hashes and exact replay;
- at least 300 trades across at least three calendar years;
- at least 75 trades per enabled side and 50 per enabled branch;
- positive net P&L and expectancy;
- net profit factor >= 1.25;
- annualized daily Sharpe >= 1.25 using every expected CME score session after the
  explicit causal warmup, including roll, fail-closed, missing, and no-trade zeros;
- IID PSR is disclosed as diagnostic only. T1 confidence is gated by the frozen
  10-session, 20,000-draw block bootstrap (seed `20260712`); DSR is calculated only
  after cross-trial Sharpe dispersion and an effective independent-trial count
  exist;
- average calendar-week result >= 0.50R and positive dollars, including zero-trade
  weeks;
- 10-session block-bootstrap `P(total net P&L <= 0) < 5%`;
- observed annualized net / absolute bootstrap p95 drawdown >= 1.0 (target 1.5);
- at least six complete half-year folds, at least 100 expected CME score sessions
  in each fold, at least 70% of those folds positive, and the final fold positive;
- both branches and every traded side positive with PF >= 1.0;
- positive after removing the top five trades and, separately, top five days;
- top-five-day contribution <= 35% of total net;
- zero state, attribution, order, or reconciliation violations;
- zero missing expected CME RTH sessions after the requested score start and zero
  snapshots assigned to an unexpected closed session; and
- bootstrap p99 drawdown disclosed.

The capital-policy limit is not a T1 primary gate. `Research-worthy` promotion
remains blocked until p99 drawdown is no more than both 25% of declared strategy
capital and 50% of its hard loss limit.

If T1 misses any primary gate, close v2 without threshold rescue. If T1 passes,
the candidate is only `primary-qualified`. Full historical promotion still
requires T2–T7 neighborhood passage, T8 adverse-fill-timing passage, T9
doubled-cost passage, and the capital policy in the permanent standard.

## Later evidence

Only a historically qualified, fully frozen candidate may be evaluated on later
data. Since 2025–2026 has already been exposed elsewhere in this project, it cannot
serve as final proof. Limited-live eligibility ultimately requires at least 100
genuinely prospective shadow trades with PF >= 1.15, daily Sharpe >= 1.0, positive
observed-cost net P&L, drawdown inside the declared limit, and exact signal/order/
state reconciliation with fill price and time inside preregistered tolerances.

Passing would authorize consideration of a capped MNQ pilot, not immediate
unattended NQ deployment.

## Human-facing controls if later ported

The TradingView menu will expose only four coherent groups:

1. **Trade size** — quantity, with one-contract research default.
2. **Execution model** — commission and slippage for honest backtests.
3. **Chart** — levels, state, entries, stops, targets, and minimal diagnostics.
4. **Frozen research preset** — read-only explanation of the v2 rules/version.

There will be no Live/Backtest classification, broker-setting confirmation,
failsafe checklist, branch deletion, hidden preset, or advanced threshold maze.
