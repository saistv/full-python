# Broker-Safe Execution Core Design

Date: 2026-07-14
Status: approved by the persistent project objective; implementation staged by
dependency order
Authority: `docs/audits/2026-07-13-principal-adversarial-red-team-audit.md`

## Purpose

Milestone 2 replaces the dormant order-capable adapter's local assumptions with
an event-driven, broker-authoritative lifecycle. It must be impossible to enter
until account identity and state are known, impossible for one logical intent
to create two orders, and impossible to call a position closed until broker
events and REST reconciliation prove it.

This work remains offline-only. The observe composition stays order-disabled,
and no order-capable composition root is added by this design.

## Nonnegotiable Invariants

1. The strategy learns position state only from authoritative fills and closed
   trades, exactly once and before its next `on_bar` call.
2. An entry is legal only from a hydrated, reconciled, stable-flat state with
   no working entry, exit, cancellation, flatten, or unknown submission.
3. Account ID and exact contract ID scope every position, order, fill,
   cancellation, and liquidation decision.
4. A durable logical intent exists before any POST. An unknown POST outcome is
   reconciled by that identity and is never retried blindly.
5. A filled position is not stable until its broker-held protection is
   confirmed for the full open quantity.
6. Session close, shutdown, DLL, and emergency flatten use one state machine:
   cancel relevant orders, submit a schema-valid contract liquidation, then
   require broker-confirmed flat and no working orders.
7. Startup and reconnect keep entries latched until positions, orders, fills,
   protection, and account risk agree across user sync and REST snapshots.
8. Partial fills and closes track remaining quantity; they never become an
   exact-quantity guess.
9. Any contradiction or sequence gap enters a durable recovery/halt state.

## Target Lifecycle

The final execution authority will distinguish these states explicitly:

- `UNHYDRATED`
- `STABLE_FLAT`
- `ENTRY_SUBMISSION_PENDING`
- `ENTRY_WORKING`
- `POSITION_UNPROTECTED`
- `POSITION_PROTECTED`
- `EXIT_CANCEL_PENDING`
- `EXIT_SUBMISSION_PENDING`
- `EXIT_WORKING`
- `FLATTENING`
- `SUBMISSION_UNKNOWN`
- `RECOVERY_REQUIRED`
- `HALTED`

Only `STABLE_FLAT` permits a new entry. State changes are driven by normalized
broker events and confirmed snapshots, not by HTTP success alone.

## Dependency-Ordered Delivery

### Slice A: Broker authority foundation - P0-02

- Move strategy fill and close callbacks behind a common
  `poll_strategy_feedback()` stream.
- Make `SimulationEngine` and `LiveLoop` dispatch that stream at the same
  lifecycle points.
- Have `TradovateBroker` emit one domain `Fill` after an entry fill has known
  protection and one domain `Trade` after an exit fill closes the fill ledger.
- Add an entry-pending state and reject repeated entries while an entry,
  position, exit, or recovery state exists.
- Restore stable-flat state after a confirmed entry rejection/cancellation.

This closes P0-02 offline and creates the event boundary required by all later
slices.

### Slice B: Identity authority - P1-02 and P0-04 prerequisite

- Resolve and persist the active Tradovate `contractId` beside account ID.
- Reject missing, foreign-account, foreign-contract, duplicated, and roll
  straddle snapshots.
- Scope order and fill events to the configured account and contract.
- Replace symbol-based liquidation bodies with the exact documented
  `accountId`, `contractId`, and `admin` schema.

### Slice C: Durable intents and unknown outcomes - P0-05

- Persist a run ID, logical intent ID, role, account/contract identity, body
  digest, and `SUBMISSION_PENDING` event with `fsync` before POST.
- Associate broker order IDs and all subsequent events with that intent.
- On transport ambiguity, enter `SUBMISSION_UNKNOWN`, query synchronized order
  state, and never resubmit until the intent is resolved.
- Recover valid journal prefixes after a torn trailing record.

### Slice D: Account synchronization and hydration - P1-01

- Add a demo-only user-sync service with sequence checking, heartbeat,
  reconnect, deduplication, and token renewal for the actual clients.
- Hydrate positions, working orders, fills, and risk snapshots before opening
  the entry latch.
- Periodically reconcile user events with account-scoped REST snapshots.
- Rehydrate after restart without assuming flat.

### Slice E: Confirmed flatten and session boundaries - P0-03/P0-04

- Trigger the backstop from the exchange calendar at close minus one minute,
  including early closes.
- Reuse the same confirmed-flatten protocol for shutdown, DLL, outage, and
  emergency recovery.
- Require flat position plus no working entry/exit/protective orders by a
  deadline, otherwise remain halted and alert externally.

### Slice F: Partial quantity and full failure matrix

- Track cumulative fill and remaining quantity for entry, stop, exit, and
  liquidation orders.
- Protect every filled entry quantity and preserve protection for residual
  quantity during partial closes.
- Run all incidents in the principal audit's adversarial failure matrix against
  a schema-strict, protocol-faithful fake server.

## Slice A Event Ordering

For every bar, orchestration is:

1. Broker processes pre-strategy state.
2. Broker lifecycle events update the order-state shadow.
3. Broker position and shadow position are cross-checked.
4. Domain feedback is dispatched to `strategy.on_fill` or
   `strategy.on_trade_closed` exactly once.
5. Strategy receives account context and processes the bar.
6. Broker applies the strategy result.
7. Steps 2-4 repeat so same-bar closes are visible before the next bar.

Simulation uses the same feedback dispatch contract. `PositionEngine` produces
domain feedback but no longer calls the strategy directly. This preserves one
event-driven ownership model instead of adding a Tradovate-only callback path.

## Slice A Acceptance

- A repeated signal while entry order 1 is working creates no second REST
  order.
- A repeated signal after entry fill and protection creates no second entry.
- A recovery/exit state creates no entry.
- Entry rejection returns to stable flat and permits a later new intent.
- Tradovate entry fill invokes strategy fill feedback once with the submitted
  intent reason; duplicate broker fill halts before a second callback.
- Tradovate exit fill invokes trade-closed feedback once with fill-derived
  broker P&L; duplicate close fill halts before a second callback.
- Simulation/PaperBroker identity remains exact on synthetic and baseline data.
- Observe mode remains unable to place orders.

## Promotion Boundary

Completing Slice A closes only P0-02 in offline code. Demo orders remain
prohibited until Slices B-F, the complete failure matrix, and the attended Gate
5 observation evidence all pass.
