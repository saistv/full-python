# Broker Authority Foundation Implementation Plan

Date: 2026-07-14
Parent design: `docs/superpowers/specs/2026-07-14-broker-safe-execution-design.md`
Scope: Slice A only; offline, no credentials, no broker connection

## Task 1: Pin the unsafe current behavior

- Add broker tests for repeated entry while the first entry is working, after
  fill/protection, during exit, and during recovery.
- Add a LiveLoop integration strategy that emits whenever it has not received
  fill feedback; prove current Tradovate behavior submits a duplicate.
- Add exact-once fill and trade-close callback assertions.

## Task 2: Define common strategy feedback

- Add `StrategyFeedback = Fill | Trade` and `poll_strategy_feedback()` to the
  broker protocol.
- Queue feedback in `PositionEngine` instead of calling strategy hooks.
- Delegate feedback from `PaperBroker`.
- Dispatch feedback in `SimulationEngine` and `LiveLoop` before the next
  strategy bar and after same-bar broker application.

## Task 3: Enforce legal entry state

- Add `ENTRY_PENDING_FILL` to the current transitional broker state enum.
- Reject entry intents unless the broker is normal, flat, has no pending exit,
  and has no working entry/exit order.
- Set entry-pending after broker acknowledgment.
- Return to normal after confirmed entry rejection or cancellation when no
  recovery condition exists.

## Task 4: Emit broker-authoritative feedback

- Normalize the entry fill to a domain `Fill` using the submitted order's
  symbol and reason after protective-stop acknowledgment.
- Queue the `Trade` returned by `FillPairingLedger.close_leg()` after an exit
  fill.
- Drain each item once.

## Task 5: Verify and record

- Run focused broker, live-loop, simulation, and identity suites.
- Run the full offline suite and baseline-backed suite.
- Perform an adversarial diff review for double callbacks, callback ordering,
  stale entry latches, and observe-mode regression.
- Add a dated decision record and update `HANDOFF.md` without changing the
  `RESEARCH-ONLY` classification.
- Commit, push, and open a focused pull request.
