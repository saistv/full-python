# Account-State Startup Hydration

**Decision:** permit an order-capable broker to leave recovery state only after
an exact, current-session, stable-flat Tradovate user-sync snapshot agrees with
an independent REST snapshot. Reconcile acknowledged restart history only by
broker-visible causal identity. Treat this as Slice D1 offline evidence, not
closure of continuous synchronization, recovery, or broker-parity findings.

Parent design:
`docs/superpowers/specs/2026-07-14-account-state-hydration-design.md`.

## Failure Reduced

An empty process previously looked flat until journal history happened to keep
it closed. That cannot establish account truth after a fresh start. A broker
order ID by itself also could not prove that a historical journal intent caused
the order, and an unqualified cash-balance number could carry the wrong trading
session into the daily-loss calculation.

## Implemented Authority Boundary

- Every order-enabled `TradovateBroker` starts `RECOVERY_REQUIRED`, including
  a fresh process with an empty journal.
- `TradovateAccountHydrator` requests `user/syncrequest` for one exact user and
  requires accounts, contracts, positions, orders, commands, command reports,
  order versions, fills, cash balances, and account risk statuses.
- Independent REST lists are normalized and compared by entity ID and
  safety-relevant fields. List ordering cannot create false disagreement.
- Exact account name/ID and contract symbol/ID are mandatory. Duplicate or
  malformed entities, nonzero foreign-contract exposure, foreign-contract
  working orders, fills without an account-order join, unknown order statuses,
  unsafe account flags, and non-normal risk state fail closed.
- Cash-balance `tradeDate` must equal the caller's expected trading session.
  The broker carries authoritative realized P&L as a pre-process baseline so a
  same-session rehydration does not double count locally paired fills. A session
  change requires fresh account hydration.
- Journal schema 2 adds a hash-covered `client_operation_id` while retaining
  verified reads of schema 1. New orders and cancels send it as `clOrdId`;
  liquidation sends it as `customTag50`. All remain at or below the documented
  64-character limit and are in the request-body digest before POST.
- A historical `ACKNOWLEDGED` intent becomes `RECONCILED` only when its client
  ID maps to the same automated broker command, the command maps to the same
  order ID, and that broker order is terminal. Legacy, missing, mismatched,
  working, pending-submission, and unknown-outcome history stays
  recovery-latched. A REST-accepted cancel can reconcile only through its exact
  command and a canceled/expired target order.
- Successful stable-flat hydration clears only transient state that the broker
  snapshot proves terminal. It also imports the current daily-loss state, so a
  preexisting account breach blocks entry before any order POST.
- Liquidation requests now also declare `isAutomated=true`; the correlation ID
  is generated per logical operation and is not treated as permission to retry
  an ambiguous submission.

## Acceptance Evidence

- Focused account-sync/journal/HTTP/broker/live-loop suite: 133 passed.
- Full offline suite: 527 passed, 5 operator-data skips.
- Full suite with `FULL_PYTHON_BASELINE_DATA`: 531 passed, 1 prospective-data
  skip.
- Golden historical, sizing, simulator/live-loop identity, and session-report
  tests all ran rather than skipping.
- Stable-flat hydration opens entries; open positions, working orders, stale
  trade dates, source disagreement, unsafe risk, malformed identity, and
  unresolved history do not.
- Restart tests prove exact command correlation reopens terminal acknowledged
  history while legacy and mismatched correlation remain closed.
- A valid schema-1 intent lifecycle reopens, verifies its original hash chain,
  and continues with hash-linked schema-2 records.
- Same-session rehydration preserves one authoritative realized-P&L total.
- A session rollover rejects stale hydration but accepts an exact snapshot for
  the incoming session, whose broker P&L replaces the prior daily-loss state.
- Snapshot identity rejects boolean and floating-point lookalikes before broker
  equality can treat them as configured integer IDs.

The real-data suite proves the frozen research behavior was not changed. Fake
user sync, REST, and order transports prove local protocol handling only; they
do not prove Tradovate delivery, event ordering, or execution parity.

## Remaining Blockers

P1-01 remains open. There is no incremental user-event cache, connection-loss
invalidation, reconnect/resubscribe state machine, heartbeat supervisor, token
client replacement, or periodic REST reconciliation. `SUBMISSION_PENDING` and
`SUBMISSION_UNKNOWN` history still require recovery and are not automatically
resolved. Inherited open positions and working orders are recognized but not
restored into a running strategy. P0-05 therefore remains open for those paths,
and the broader P1-02 remains open until runtime composition resolves and
persists identity.

P0-04 also remains open because liquidation HTTP acknowledgement is not
broker-confirmed flat state with zero working orders. Partial quantities,
confirmed session/shutdown flatten, and the complete adversarial failure matrix
remain later slices.

No credentials, networked broker composition, or order-capable runner was
added. The project remains **RESEARCH-ONLY** and no demo or funded order is
authorized.
