# Account Synchronization Runtime Design

Date: 2026-07-15
Status: approved by the persistent project objective
Parent: `2026-07-14-broker-safe-execution-design.md`, Slice D
Predecessor: `2026-07-14-account-state-hydration-design.md`

## Purpose

Startup hydration is only a point-in-time fact. It becomes stale as soon as a
user event arrives, a trading WebSocket disconnects, a token is replaced, or a
REST cross-check is missed. This slice adds an offline, fail-closed runtime that
owns that validity boundary for one exact Tradovate account and contract.

The runtime does not add credentials, an order-capable composition root, or a
retry loop that can submit orders. It remains a protocol-tested dependency for
later DEMO lifecycle work.

## Current Protocol Facts

- Tradovate documents `user/syncrequest` as the real-time authority for account,
  position, order, fill, cash, and risk changes.
- Current Partner API documentation requires `splitResponses` and says the
  caller must explicitly request `entityTypes`; the default entity filter is
  empty.
- An account filter may be combined with `entityTypes`. User filters must not be
  combined with entity filters under the current contract.
- Property updates are `e="props"` messages with `entityType`, `eventType`, and
  `entity`. `entity` may be one object or an array. Event types are `Created`,
  `Updated`, or `Deleted`.
- `e="shutdown"` announces maintenance or quota shutdown. It is not a normal
  data update and invalidates the connection.
- No monotonic event sequence or replay cursor is documented. The runtime must
  not invent gap recovery from timestamps or arrival order.
- Tradovate requires client heartbeat text `[]` every 2.5 seconds and recommends
  connection monitoring and reconnection.

Primary references:

- https://partner.tradovate.com/overview/core-concepts/web-sockets/user-syncrequest
- https://partner.tradovate.com/overview/core-concepts/architecture-overview
- https://partner.tradovate.com/resources/reference/best-practices
- https://github.com/tradovate/example-api-faq/tree/main/example-code/user-sync-request

## Authority Model

The runtime has three states:

- `DISCONNECTED`: no account stream is trustworthy.
- `RECOVERY_REQUIRED`: a connection may exist, but account state is dirty,
  incomplete, stale, malformed, or disagrees with REST.
- `SYNCHRONIZED`: a complete current-session cache agrees with fresh REST and
  the broker has accepted the resulting hydration snapshot.

Only `SYNCHRONIZED` permits the broker hydration latch to be open. The broker
also retains all of its own entry, risk, position, and order-state gates.

## Startup And Replacement

1. Invalidate broker hydration before opening or replacing a connection.
2. Build a new authorized REST client and a new trading WebSocket client from
   the current token. Never mutate a connected client's token in place.
3. Authorize the trading WebSocket with the trading access token.
4. Request exact-account user synchronization with `splitResponses=true` and
   every safety-critical entity type.
5. Require a complete initial dataset. Missing or permission-filtered critical
   collections fail closed.
6. Build an entity-ID cache and independently compare it with fresh REST.
7. Hydrate the broker only from the resulting current-session snapshot.

A renewed token must retain the same positive user ID. Renewal failure, client
construction failure, authorization failure, incomplete sync, or REST mismatch
leaves the old state invalid and propagates an error.

The current public documentation does not define a completion envelope for
multiple split initial responses. This implementation requests the required
mode and accepts only a complete `SyncMessage`; an incomplete first response
fails closed. Capturing and pinning the actual DEMO split envelope is an
explicit prerequisite before order-capable promotion.

## Incremental Cache Rules

- Only the requested safety-critical entity types are accepted.
- Every entity must be an object with a strict positive integer `id`.
- An event carrying an array is validated and applied atomically.
- `Created` requires an absent ID. An exact duplicate is idempotent; a
  conflicting duplicate is an error.
- `Updated` requires an existing ID. An exact duplicate is idempotent. An
  update for an unknown ID is treated as a gap and fails closed.
- `Deleted` requires an existing ID and removes it. A repeated or unknown
  deletion fails closed.
- Unknown event kinds, entity types, event types, malformed shapes, shutdown,
  transport errors, and liveness failure invalidate broker hydration before
  raising.

Every accepted property event marks the cache dirty and immediately invalidates
broker hydration. The runtime does not infer safety from the event alone.
Fresh REST agreement is required before returning to `SYNCHRONIZED`.

## Heartbeat And Liveness

The runtime sends `[]` at most every 2.5 seconds even when no application event
is present. Receive waits are capped at the next heartbeat deadline. The real
transport records the monotonic time of every complete inbound frame, including
SockJS heartbeat frames. If inbound transport activity exceeds the configured
liveness deadline, state is invalidated and the connection is closed.

## Periodic Reconciliation

At a bounded interval, and whenever the cache is dirty, the runtime compares
the full cached safety view with independent REST. Agreement produces a new
hydration snapshot; disagreement or malformed data remains recovery-required.
There is no automatic order retry and no timestamp-based conflict resolution.

## Conservative Boundary

This slice can maintain or restore a stable-flat synchronized session. It
recognizes exposure and working orders but still cannot reconstruct a running
strategy around inherited or partially filled state. Any such state remains
recovery-required. Broker-confirmed flatten, partial quantities, unknown POST
resolution, and the complete failure matrix remain later slices.

## Acceptance

- Current user-sync payload contains exact account scope, split mode, and all
  required entity filters.
- Stable-flat startup reaches `SYNCHRONIZED`; no entry can occur beforehand.
- Any valid property update immediately closes the broker latch until REST
  reconciliation succeeds.
- Duplicate identical events are idempotent; conflicting or missing-history
  events fail closed.
- Shutdown, close, malformed frame/event, stale liveness, failed token renewal,
  or user-ID change invalidates state before propagating.
- Token renewal replaces both REST and WebSocket clients and requires a fresh
  full sync.
- Heartbeats are emitted on schedule and receive waits cannot starve them.
- Periodic REST agreement can reopen only stable-flat state.
- Historical simulation and the frozen strategy remain unchanged.

## Promotion Boundary

Passing these tests is offline protocol evidence only. P1-01 remains open until
the actual DEMO split-response envelope is captured, the runtime is composed
with real clients, and disconnect/reconnect drills pass. P0-05 remains open for
unknown outcomes and inherited exposure. No demo or funded order is authorized.
