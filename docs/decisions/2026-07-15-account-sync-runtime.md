# Incremental Account Synchronization Runtime

**Decision:** add a fail-closed runtime around the stable-flat startup hydrator.
The runtime may reopen broker authority only after a complete exact-account
user-sync cache agrees with fresh REST. Treat this as Slice D2 offline protocol
evidence, not Tradovate DEMO parity or authorization to submit orders.

Design:
`docs/superpowers/specs/2026-07-15-account-sync-runtime-design.md`.

## Failure Reduced

Slice D1 established account truth only at startup. That truth could remain
apparently valid after a property update, websocket disconnect, stale socket,
token replacement, or later REST disagreement. The old websocket layer also
had no public application-heartbeat operation or inbound-activity timestamp.

## Implemented Authority Boundary

- Current `user/syncrequest` requests are scoped to the configured account,
  set `splitResponses=true`, and explicitly request all ten safety-critical
  entity types. The complete initial collections are detached from the vendor
  response and retained for cache construction.
- `AccountEntityCache` indexes every required collection by strict positive
  entity ID. Created, Updated, and Deleted property events are applied
  atomically. Exact duplicate creates and updates are idempotent; conflicting
  creates, unknown updates/deletes, malformed arrays, unknown types, and
  unknown event kinds fail closed.
- `TradovateBroker.invalidate_account_state()` publicly closes the hydration
  latch. Every property event invalidates the broker before cache mutation or
  REST access. Only a complete normalized cache-to-REST match can hydrate the
  stable-flat broker again.
- `TradovateAccountSyncRuntime` has explicit `DISCONNECTED`,
  `RECOVERY_REQUIRED`, and `SYNCHRONIZED` states. Startup and explicit restart
  always create fresh websocket and REST clients, authorize the websocket, run
  a complete sync, and compare fresh REST before opening authority.
- Token renewal invalidates and closes the old connection first. The renewed
  token must retain the exact user ID; both clients are replaced and a full
  hydration is mandatory. Renewal, authorization, identity, construction, and
  hydration failures remain closed and propagate without order retry.
- The framing client can send Tradovate's required `[]` heartbeat. The low-level
  transport timestamps every complete inbound websocket frame, including
  SockJS heartbeats, and the runtime bounds receive waits by heartbeat and REST
  reconciliation deadlines. Stale inbound activity closes the connection.
- Valid property changes and bounded periodic checks compare the entire cached
  safety view with independent REST. Shutdown, unknown events, cache gaps,
  liveness loss, or REST disagreement invalidates and closes the connection.

## Acceptance Evidence

- Focused account-runtime/account-sync/broker/auth/websocket/transport suite:
  199 passed.
- Full offline suite: 563 passed, 5 operator-data skips.
- Full suite with `FULL_PYTHON_BASELINE_DATA`: 567 passed, 1 prospective-data
  skip.
- Python compilation succeeds with a sandbox-local bytecode cache.
- Stable-flat startup, property-update ordering, atomic event batches,
  idempotent replay, shutdown, unknown-history gaps, heartbeat deadlines,
  stale liveness, periodic REST drift, renewal success/failure, token identity
  change, authorization failure, explicit restart, and close are covered.
- Golden historical, sizing, simulator/live-loop identity, and session-report
  tests ran against the frozen nine-month anchor and remained green.

## Remaining Blockers

Current Tradovate Partner documentation requires split responses but does not
document the multi-response completion envelope. This implementation accepts
only a complete initial response and fails closed otherwise. P1-01 remains open
until the actual DEMO envelope is captured and pinned, real connection-loss and
reconnect drills pass, and heartbeat behavior is proven across blocking REST
calls rather than inferred from fakes.

The runtime intentionally restores only stable-flat state. It does not rebuild
strategy state around inherited positions or working orders, resolve unknown
POST outcomes, prove broker-confirmed flat after liquidation, or compose an
order-capable runner. P0-04, P0-05, and the remaining P1-01/P1-02 operational
paths therefore stay open.

No credentials, networked broker composition, or order-capable runner was
added. The project remains **RESEARCH-ONLY** and no demo or funded order is
authorized.
