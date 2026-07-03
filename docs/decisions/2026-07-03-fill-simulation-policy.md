# Fill Simulation Policy

## Decision

The simulation engine converts order intents into fills under explicit, conservative,
documented rules. Fidelity failures in past Python backtests (a 23% P&L overestimate and a
$63K directional error on an exit variant) came from implicit fill assumptions. Every rule
below is chosen to make the simulated result **no better than reality**, and every
assumption is recorded in the event ledger so mismatches can be audited per trade.

## Fill timing

- Default: `next_bar_open`. A decision made on bar close fills at the **next bar's open**,
  plus adverse slippage. This models signal → webhook → broker latency.
- Reconciliation mode: `signal_bar_close`. Fills at the signal bar's close, plus adverse
  slippage. This exists **only** to reconcile against legacy TradingView runs that used
  `process_orders_on_close=true`. It must never be used for promotion decisions.
- The active mode is part of the simulation config hash, so a report always states which
  timing produced it.

## Intrabar ambiguity — worst case wins

A 1-minute OHLC bar cannot order its own high and low. When a resting stop and any
favorable exit level are both inside one bar's range, the engine assumes the **stop filled
first**. Every such fill is flagged `ambiguous=true` on its fill event and trade record,
and reports count ambiguous exits so their share of P&L is always visible.

## Stop enforcement

- Stops are frozen at entry (matching the live broker-bracket architecture).
- If a bar **opens** through the stop (gap), the exit fills at the open, not the stop price.
- Otherwise, if the bar's range touches the stop, the exit fills at the stop price.
- Exit slippage is applied adversely in both cases.

## Session risk gate

- Entries are permitted only during RTH (9:30–16:00 ET) when `rth_entries_only` is set
  (default on, matching the RTH-first promotion rule).
- Open positions are flattened at the configured backstop time (default 15:59 ET) at that
  bar's close with exit slippage — a market order sent at the backstop, not a free exit.
- If a session ends without the backstop firing (half-days, data gaps), the position is
  closed at the last bar of that session with reason `session_end`.
- A position left open at the end of the data closes at the final bar with reason
  `end_of_data` and is flagged; survivability reports must not silently include it as a
  normal trade.

## Costs

- Commission and slippage are always applied. There is no zero-cost mode.
- Defaults are conservative and MNQ-first: entry slippage 1.0 pt (4 ticks), exit slippage
  0.5 pt (2 ticks), extra 1.0 pt entry slippage during the first 15 minutes of RTH,
  matching observed NQ execution behavior from the legacy research library.

## Deterministic event order (per bar)

1. `bar` event.
2. Session-end flatten for a position held across a session boundary (closed at the prior
   bar's close, reason `session_end`).
3. Stop gap check at the open for an existing position.
4. Pending entry fills at the open (from intents accepted on the prior bar).
5. Pending exit fills at the open (from exit decisions on the prior bar). A resting
   market order executes at the open, which by definition precedes any intrabar path —
   this ordering is factual, not optimistic.
6. Intrabar stop (worst case first), then intrabar target, against the bar's range for
   whichever position remains. If both stop and target sit inside one bar's range, the
   stop fills and the trade is flagged ambiguous.
7. Backstop flatten at the bar close if the flatten time is reached.
8. Strategy `on_bar` at the close; its signals, rejections, intents, and exits are logged.
9. Risk gate accepts or vetoes new intents; accepted intents queue for the next bar.

Two runs over the same data, config, and code must produce byte-identical event logs.

## Consequences

- Simulated results are pessimistic relative to naive backtests. That is intentional.
- TV reconciliation (M2) diagnoses drift by category: fill timing, ambiguity policy, cost
  model — each is a switch or a flagged event, not a guess.
