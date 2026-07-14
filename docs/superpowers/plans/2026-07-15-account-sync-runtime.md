# Account Synchronization Runtime Implementation Plan

Date: 2026-07-15
Design: `docs/superpowers/specs/2026-07-15-account-sync-runtime-design.md`

## Task 1: Current sync contract

- Send exact account scope, `splitResponses=true`, and explicit critical
  `entityTypes`.
- Expose complete initial collections for cache construction while retaining
  the existing hydration API.
- Add strict tests for request shape and incomplete initial responses.

## Task 2: Cache and broker validity

- Add a strict account entity cache with atomic property-event application.
- Add a public broker invalidation boundary.
- Mark every accepted update dirty and close the broker latch before any
  reconciliation attempt.
- Test idempotent duplicates, conflicts, unknown IDs, deletes, arrays, and
  malformed events.

## Task 3: Runtime lifecycle

- Add explicit disconnected, recovery-required, and synchronized states.
- Build fresh REST/WebSocket clients per token and fully rehydrate on startup,
  reconnect, and renewal.
- Add proactive heartbeat scheduling and inbound-activity liveness checks.
- Add bounded periodic REST reconciliation.
- Test shutdown, disconnect, liveness loss, renewal failure, identity change,
  and successful client replacement.

## Task 4: Evidence and publication

- Run focused, full offline, and baseline-backed suites.
- Adversarially review event ordering, stale-cache reopening, and retry paths.
- Update `HANDOFF.md` and add a dated decision record without claiming DEMO
  parity.
- Commit, push, and open a focused pull request.
