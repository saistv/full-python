# Account-State Hydration Design

Date: 2026-07-14
Status: approved by the persistent project objective
Parent: `2026-07-14-broker-safe-execution-design.md`, Slice D

## Purpose

An order-capable process must never infer that an account is flat from empty
local memory. This slice creates the first broker-authoritative startup gate:
Tradovate user sync supplies the initial entity cache, account-scoped REST
lists independently cross-check it, and the broker opens entries only when the
combined evidence proves one exact account and contract are tradable and
stable flat.

The work remains offline-only. It adds no credentials, order-capable command,
or live composition root.

## Official Protocol Facts

- Tradovate recommends `user/syncrequest` over the trading WebSocket for
  real-time user data and requires client heartbeat frames every 2.5 seconds:
  https://partner.tradovate.com/resources/reference/best-practices
- The official example sends `{users: [userId]}`. The first response contains
  entity arrays including accounts, positions, orders, commands,
  orderVersions, fills, cashBalances, and accountRiskStatuses. Later messages
  contain `entityType`, `eventType`, and `entity`:
  https://github.com/tradovate/example-api-faq/tree/main/example-code/user-sync-request
- Property events are `Created`, `Updated`, or `Deleted`. The protocol does not
  document a monotonic event sequence:
  https://partner.tradovate.com/overview/core-concepts/architecture-overview
- Order, fill, and position entities expose exact account/order/contract
  identity through their documented list endpoints. A fill has no account ID,
  so it must be joined through its order:
  https://partner.tradovate.com/api/rest-api-endpoints/orders/order-list
  https://partner.tradovate.com/api/rest-api-endpoints/orders/fill-list
  https://partner.tradovate.com/api/rest-api-endpoints/positions/position-list
- Place-order and cancel commands accept `clOrdId` up to 64 characters.
  Liquidation accepts `customTag50` up to 64 characters. Commands expose the
  supplied client identifier:
  https://partner.tradovate.com/api/rest-api-endpoints/orders/place-order
  https://partner.tradovate.com/api/rest-api-endpoints/orders/cancel-order
  https://partner.tradovate.com/api/rest-api-endpoints/orders/liquidate-position
  https://partner.tradovate.com/api/rest-api-endpoints/orders/command-list

Because no monotonic sequence is documented, this design does not fabricate
one. Disconnect, shutdown, malformed events, or uncertain ordering invalidate
hydration. Recovery requires a fresh full sync and REST comparison.

## Startup Protocol

1. Construct every order-capable broker in `UNHYDRATED` / recovery state.
2. Resolve the configured account name/ID and contract symbol/ID against REST.
3. Authorize the trading WebSocket and request `user/syncrequest` for the exact
   authenticated user.
4. Require the initial response to contain every safety-critical collection.
5. Fetch independent REST snapshots for accounts, positions, orders, commands,
   order versions, fills, cash balances, and account risk status.
6. Normalize both sources by entity ID and compare safety-relevant fields.
7. Reject missing identity, duplicates, foreign account exposure, foreign
   contract working orders, unknown order statuses, fills that cannot be joined
   to an account order, disabled account flags, or non-normal risk state.
8. Require the cash-balance `tradeDate` to match the expected trading session;
   a realized-P&L number without current session identity is not authoritative.
9. Reconcile durable journal history by broker-carried client order ID. Legacy
   journal history without such an ID remains recovery-latched.
10. Open the entry latch only if the exact account has no position, no working
   or transitional order, no unresolved intent, and trustworthy daily realized
   P&L context.

## Causal Correlation

Every new mutation receives a random, opaque `fp-<uuid>` client operation ID
before the journal append. The ID is persisted in the hash-linked journal and
sent as:

- `clOrdId` for new orders and cancel commands;
- `customTag50` for liquidation.

The request body digest includes the transmitted ID. A response timeout can
therefore be matched to a command/order without timestamp or side heuristics.
The identifier is not a retry key: unknown outcomes remain non-retryable until
broker reconciliation proves the effect.

An acknowledged historical intent is reconciled only when its client operation
ID resolves to the same automated broker command, that command resolves to the
same broker order ID, and the order is terminal. An order ID alone is not
sufficient causal evidence. A REST-accepted cancel may likewise reconcile only
when its exact client ID resolves to the canceled/expired target order. Pending
submission and unknown-outcome states remain recovery-required.

## Conservative Slice Boundary

This slice may reopen a fresh or safely reconciled **stable-flat** startup. It
must recognize non-flat and working-order snapshots, but it does not restore a
running strategy around an inherited position. That later capability requires
open-position strategy-state restoration and partial-quantity work. Until
then, those snapshots remain recovery-required and are available for operator
reconciliation.

Incremental event pumping, reconnect/resubscribe, token-client replacement,
and periodic online reconciliation remain the next Slice D substage. The
absence of those pieces must stay visible in the handoff and audit register.

## Acceptance

- A new order-capable broker cannot submit before hydration.
- Exact stable-flat user-sync and REST snapshots open the entry latch.
- Any account/contract mismatch, open position, working order, unresolved
  intent, incomplete source, or unsafe risk/account flag keeps it closed.
- REST and user-sync disagreement fails closed.
- Cash-balance trade date must match the active trading session, and account
  realized P&L is not double counted after same-session rehydration.
- An already-breached hydrated daily loss limit blocks entries before REST.
- Every new order/cancel/liquidation mutation carries a durable broker-visible
  correlation ID before POST.
- Unknown outcomes are not retried and can be associated without heuristics.
- Observe mode and historical simulation remain unchanged.

## Promotion Boundary

Passing this slice is offline evidence for startup stable-flat hydration only.
P1-01 remains open until incremental sync, reconnect, token renewal, and
periodic REST reconciliation are integrated and tested. P0-05 remains open for
legacy and inherited non-flat recovery. No demo or funded order is authorized.
