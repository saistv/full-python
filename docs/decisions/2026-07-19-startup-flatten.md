# Startup inherited-state flatten (P1-8) — IMPLEMENTED OFFLINE

Date: 2026-07-19
Design: `docs/superpowers/specs/2026-07-19-startup-flatten-design.md`
Audit: `docs/audits/2026-07-13-adversarial-audit.md` (P1-8)
**Operator policy decision (2026-07-19): FLATTEN.** An inherited position or
working order set found at startup is closed, never adopted for trading.
Adopt-and-protect was explicitly rejected.

## Context

P1-8's original hazard ("adapter starts flat and will double a live
position") was structurally closed by Slice D1: an order-capable broker
refuses to open with inherited state — `hydrate_account_state` raises and
latches. What remained was the recovery flow: turning that dead-end halt
into a confirmed, journaled close followed by a fresh stable-flat start.

## What changed

- `TradovateBroker.startup_flatten(snapshot, *, timestamp_utc)`: requires
  `flatten_enabled`; verifies the same account/contract identity as
  hydration (shared `_require_snapshot_identity`); registers inherited
  working orders under `ROLE_INHERITED`; adopts the snapshot position solely
  so the **Slice E confirmed-flatten protocol** (shared `_begin_flatten`
  core) can close it — journaled cancels first, liquidation only after every
  cancel confirms, flat-plus-no-working-orders verified. Misuse with a
  stable-flat snapshot raises.
- **No strategy trade is fabricated**: an exit fill with no open ledger leg
  (only possible for an inherited position) skips `close_leg` and emits no
  strategy feedback; realized P&L re-enters through the account's own
  records at the next hydration.
- **Resolution stays `RECOVERY_REQUIRED`**: entries reopen only through a
  fresh stable-flat `hydrate_account_state` against new sync+REST agreement
  (test-proven end-to-end: startup flatten → confirmed flat → terminal
  journal-correlated snapshot → `NORMAL`). A latched resolve no longer
  leaves a stale `FLATTEN_*` execution state (bug found by these tests).
- Races and failures inherit Slice E semantics: an inherited stop filling
  before its cancel closes the position with the liquidation never
  submitted; cancel failure halts latched with protection standing; a
  non-closing inherited fill (exposure grows) halts on the wrong-side guard.
- `live/order_runner.run_startup_flatten(broker, pump, ...)` drives the
  protocol via the OrderEventPump before LiveLoop starts, with a wall-clock
  deadline that halts for operator review; the Slice E per-bar deadline
  remains the backstop if LiveLoop ever started early.

Startup sequence in the composition root: `hydrate_with_state()` →
inherited state? → `startup_flatten` + `run_startup_flatten` → fresh
`hydrate_with_state()` → `hydrate_account_state` → entries may reopen.

## Evidence

- 6 new broker tests + 2 runner tests; full staged sequence, race, misuse,
  identity, cancel-failure, non-closing-fill, deadline all covered.
- Offline suite **769 passed / 5 skipped**; 9-month anchor suite
  **773 passed / 1 skipped**. Sim/paper identity untouched.

## Still open

Slice F (partial quantities + full adversarial failure matrix — inherited
partial quantities included there), P2-5, the observe runner's
`accounts[0]`, P1-01's real DEMO envelope, P0-04's REST leg, all attended
Gate 5+ drills. Nothing may trade live; the Gate 5 boundary literals are
unchanged.


---

## CORRECTION (2026-07-19, after the independent review — PR #35)

The unqualified P1-8 closure exceeded the supported state space: inherited
ENTRY-side fills routed to the exit handler and raised while the real
account held new exposure; multi-contract inherited positions poisoned
terminal state on partial liquidation fills; and the stop-wins-cancel race
could not pass fresh hydration (Filled was not an accepted cancel-terminal
status). Fixed in Slice H (H4/H5, PR #37): the state space is enforced at
the `startup_flatten` boundary (quantity-1 only; no exposure-increasing
inherited orders), an inherited fill while flat adopts-then-flattens, and
hydration accepts Filled as cancel-terminal. The recovery sequence is now
also reachable from source via the order runner (H9). Pinned as
`test_review_2026_07_19_p0_4*`/`p1_3*`.
