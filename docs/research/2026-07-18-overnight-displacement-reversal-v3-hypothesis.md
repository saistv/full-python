# Overnight Displacement Reversal v3 — Preregistered Hypothesis

**Frozen before scored replay:** 2026-07-18  
**Candidate:** `overnight_displacement_reversal_v3`  
**Primary trial:** T1  
**Requested historical window:** 2021-03-16 through 2024-12-31  
**Promotion authority:** `docs/specs/2026-07-17-automation-worthiness-standard.md`

## Thesis

The prior regular-session close is the most recent RTH consensus price. When the
next RTH opens materially away from it after at least half of overnight minute
closes were on the same side, the overnight session is directionally displaced.
If RTH first
extends that displacement but then produces a decisive completed close through
the 09:30 opening price, the extension has failed. The hypothesized trade is a
correction toward the prior RTH close.

`Overnight displacement breadth` is a price-based proxy, not observed trader,
dealer, or market-maker inventory. One-minute OHLCV has no participant holdings,
order book, aggressor side, or true volume-at-price. The inventory-correction
story is an economic interpretation that T1 is allowed to reject, not a fact
asserted by the implementation.

This is not an iteration or slice of Adaptive Trend, Opening Auction Regime v1,
or Level-Retest v2. It uses no ADX, RSI, moving average, squeeze, momentum score,
generic VWAP-distance fade, 30-minute opening range, external high/low sweep,
15-minute auction classifier, level retest, or post-hoc v2 reference/side result.
Both gap directions use one inseparable mirrored mechanism.

## Causal reference state

For CME session `t`, define:

- `P`: close of the final expected RTH minute in session `t-1`;
- `D`: median true range of the most recent 20 complete non-roll RTH sessions;
- `O`: open of the 09:30 one-minute bar in session `t`;
- `s = +1` for `O > P`, and `s = -1` for `O < P`;
- `gap_dtr = abs(O - P) / D`; and
- `displacement_breadth`: count of overnight minute closes satisfying
  `s × (close - P) > 0`, divided by all observed overnight minute bars. Ties stay
  in the denominator and not the numerator.

The overnight window is exactly 18:00–09:29 ET for the current CME session.
Bars from 16:00–17:59 are never treated as overnight evidence. Complete overnight
coverage requires at least two bars, a first bar no later than 18:05, a final bar
no earlier than 09:24, no consecutive timestamp gap greater than 15 minutes,
finite OHLCV, and positive total volume. Overnight high, low, close, range, VWAP,
volume, and both directional breadths are diagnostics; only the breadth aligned
with `s` is a gate.

The previous RTH session is complete only when it contains every expected minute
from 09:30 through the calendar-specific final bar: 15:59 on ordinary days,
12:59 on abbreviated holiday sessions, or 13:14 on scheduled early closes. An
incomplete prior RTH session clears its close/reference permission and cannot be
silently replaced by an older close.

Continuous-contract identity is inferred from the frozen front-contract roll
rule because the source symbol is `NQ1!`. A roll-transition session fails closed.
Its complete RTH can establish the new contract's next prior close, but its range
does not enter `D`.

## Eligibility frozen with the 09:30 bar

The strategy processes completed one-minute bars. When the bar timestamped 09:30
is processed, it may use that bar's `open` to freeze eligibility and its completed
high/low/close to begin the event sequence. It cannot fill at the 09:30 open; the
earliest possible fill is the next bar's 09:31 open.

A session is eligible only when all conditions hold:

- current and prior RTH sessions exist in the exchange calendar;
- `P`, `D`, `O`, and all required overnight values are finite and `D > 0`;
- the prior RTH and current overnight windows are complete;
- no continuous-contract roll transition exists;
- `0.05 <= gap_dtr <= 0.75`; and
- displacement breadth aligned with `s` is at least `0.50`.

Equality at either gap bound or the breadth threshold passes. A zero gap cannot
choose a side. Every ineligible or ambiguous state is `no_trade` with one explicit
reason, and the frozen setup receives a deterministic audit ID
`odr-v3:{session_date}:{correction_side}`.

## State machine and chronological trigger

```text
observe_overnight
→ eligible_gap | no_trade
→ wait_extension
→ wait_rejection
→ entry_pending
→ position
→ done
```

There is at most one attempt and one filled trade per session. Search completed,
contiguous RTH bars from 09:30 through 10:59. Any missing minute in the active
sequence cancels the session. Track the most extreme RTH price from 09:30 through
the current bar.

An extension attempt arms when:

- up gap: `highest_RTH_high >= O + 0.02 × D`; or
- down gap: `lowest_RTH_low <= O - 0.02 × D`.

The first completed close at least `0.01 × D` through `O` opposite the gap is
decisive:

```text
s × (close - O) <= -0.01 × D
```

If that first decisive close occurs before extension armed, cancel. Extension and
rejection may occur in the same bar because the bar's directional high/low occurs
before its final close. Do not search for a later, prettier rejection.

The correction objective must remain untraded before entry. For an up-gap short,
cancel as soon as any RTH low touches or passes `P`; for a down-gap long, cancel
when any RTH high touches or passes `P`. A signal bar that also touches `P`
cancels rather than assuming a favorable intrabar ordering.

On the decisive rejection bar, require positive range and correction-side close
location at least `0.65`:

- down-gap long: `(close - low) / (high - low)`;
- up-gap short: `(high - close) / (high - low)`.

A failed close-location check ends the session. The trigger uses no VWAP or trend
permission filter.

## Structural bracket and exits

- Up gap produces a short; down gap produces a long.
- Long stop: lowest RTH low from 09:30 through signal minus `0.02 × D`, rounded
  down to the NQ tick.
- Short stop: highest RTH high through signal plus `0.02 × D`, rounded up.
- Decision-close risk must lie in `[0.05, 0.20] × D`.
- `P` must remain in the profitable direction and offer at least `1.25R` from the
  decision close after tick rounding.
- Target distance is `min(distance to P, 2.0 × decision risk)`. A long target
  rounds down and a short target rounds up, toward the current market.
- Emit at the decisive bar close and use the next-bar-open fill model.
- Signal the time exit on the 11:59 bar for an expected 12:00 next-open fill.
- One NQ contract, flat sizing; $20/point; $10 round-trip commission; 0.75 point
  adverse entry and exit slippage; no separate RTH-open surcharge.
- Static stop and target; no re-entry, scaling, partial exit, trailing,
  breakeven, martingale, daily-loss signal filter, or discretionary switch.
- A bar touching stop and target resolves stop first.

The frozen bracket must also be valid relative to the actual next-open fill:

```text
long:  stop < fill < target
short: target < fill < stop
```

The simulator already cancels when the fill gaps through the stop but does not
cancel a target behind the fill. The report therefore treats either event as a
fatal reconciliation violation. No result containing one can pass T1, regardless
of aggregate performance.

## Data and evidence boundary

The sealed runner must construct no `MarketBar` at or after the CME session
beginning 2025-01-01. Future rows are inspected only for canonical timestamp
ordering, never parsed into strategy prices or volume. Timestamp input must be
minute-aligned canonical UTC and strictly increasing.

The requested start is 2021-03-16. Scoring begins only after causal history is
ready and no later than the 25th expected CME RTH session; every expected session
from the requested start remains in the coverage audit. After warmup, all missing,
roll, ineligible, fail-closed, and no-trade sessions enter daily and weekly series
as zero. Zero missing expected sessions and zero unexpected closed-session
snapshots are mandatory.

This history is selection-contaminated by earlier project research. It is
development evidence, never genuine confirmation. Passing all historical stages
would authorize a newly frozen prospective shadow test, not deployment.

## Nine-trial budget

T1 is the frozen default above. T2–T9 are forbidden unless T1 clears every
normal-cost primary gate:

1. T1 — defaults.
2. T2 — minimum gap `0.04D`.
3. T3 — minimum gap `0.06D`.
4. T4 — extension requirement `0.01D`.
5. T5 — extension requirement `0.03D`.
6. T6 — final signal 10:29.
7. T7 — final signal 11:29.
8. T8 — one additional completed one-minute bar of entry latency.
9. T9 — $20 round trip and 1.50 points adverse slippage per side.

T2–T7 are a neighborhood, not candidate selection. They pass only when every
trial stays net positive, median PF is at least 1.15, at least four of six trials
have PF at least 1.10, and none retains fewer than half of T1's trades. T8 is the
mandatory adverse-fill-timing stress and T9 the doubled-cost stress; each must
stay net positive with PF at least 1.10. None can replace or rescue T1.

Candidate-family DSR is computed only after actual cross-trial Sharpe dispersion
and a defensible effective independent-trial count exist. Trial budget alone is
never a DSR input. DSR is not a T1 gate, but DSR below 95% or unavailable blocks
advancement beyond `shadow-worthy`.

## T1 primary gates

All checks are conjunctive:

- two fresh replays with exact ledger, trade, snapshot, diagnostic, and research
  core hashes;
- exact code, config, simulation, evaluation-policy, data, specification, result,
  and artifact provenance;
- causal warmup completed within 25 expected sessions;
- zero missing expected sessions, unexpected closed-session snapshots, or active
  RTH minute gaps;
- at least 300 trades spanning at least three calendar years;
- at least 75 long and 75 short trades;
- positive net P&L and expectancy;
- net profit factor at least 1.25;
- annualized daily Sharpe at least 1.25 across every score-session zero;
- average calendar week at least `0.50R` and positive dollars across zero weeks;
- 10-session, 20,000-draw block-bootstrap
  `P(total net P&L <= 0) < 5%`, seed `20260712`;
- observed annualized net divided by absolute bootstrap p95 drawdown at least 1.0;
- at least six complete half-year folds with at least 100 score sessions each,
  at least 70% positive, and the final chronological fold positive;
- long and short books each net positive with PF at least 1.0;
- positive net after removing the top five trades and, separately, top five days;
- top-five-day contribution no more than 35% of total net;
- bootstrap p99 drawdown disclosed; and
- zero state, attribution, risk-veto, fill-time bracket, setup-ID,
  signal/order/fill/trade, exit, quantity, timing, or P&L reconciliation violation.

IID PSR is diagnostic only. T1 confidence is the block bootstrap. The capital
policy is not a T1 gate because no strategy-capital and hard-loss-limit pair has
been supplied. `Research-worthy` promotion remains blocked until adverse p99
drawdown is no more than both 25% of declared strategy capital and 50% of its hard
loss limit.

Any T1 failure closes v3. Do not lower a threshold, extend the entry window,
remove a side, reuse a favorable diagnostic slice, or acquire an alternate date
slice to rescue the version.

## Mandatory diagnostics

The session funnel records expected, snapshot, prior-RTH complete, overnight
complete, non-roll, gap eligible, displacement aligned, extension armed,
decisive cross, objective untouched, close-location pass, bracket pass, intent,
fill or fill invalidation, and trade close.

Reports disclose:

- every classification, cancellation, rejection, and terminal-untraded reason;
- DTR, gap points/DTR/direction, displacement breadth on both sides, overnight
  close/VWAP/range/volume/high/low relative to `P`;
- extension magnitude and signal time, decisive-cross distance, close location,
  structural extreme, decision risk/DTR, and target distance/R;
- actual fill-relative risk/reward, adverse entry gap, target behind fill,
  fill invalidation, realized net R, commission and slippage drag;
- trade mean/median, MFE/MAE, holding time, exposure, loss streaks, exit reason;
- side, year, half-year, month, weekday, signal-time, gap-size, breadth, roll,
  and abbreviated-session slices; and
- synchronized fire-day overlap and daily-P&L correlation with other strategies,
  if available, as diagnostics only and never a selection gate.

## Human-facing controls if later ported

A later TradingView port would expose only trade size, honest execution costs,
minimal chart visibility, and the frozen version explanation. It would not expose
Live/Backtest classification, broker-confirmation checklists, failsafe menus,
direction switches, hidden presets, or a threshold maze. A Pine port is forbidden
unless the Python candidate completes every historical promotion stage.
