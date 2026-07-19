# Red-team remediation (Slice H) — design and task order

Date: 2026-07-19
Input: `docs/audits/2026-07-19-independent-review.md` (independent review at
`3eab505`, merged as PR #35). All findings ACCEPTED after spot verification
(P0-1's zero-timeout `receive_event` trace, P0-2B's missing dedupe/flatten
check, and the `bars_until` maintenance placement were re-verified line by
line; the remainder are consistent with the code and the reviewer's executed
probes). Framing agreed: pre-enable code-correctness failures — the pinned
Gate 5 literals were load-bearing exactly as designed — but four "CLOSED"
claims from PRs #30-#33 were overstated and are corrected here.

## Method rule for this slice

**Fakes become faithful BEFORE fixes are written.** The review showed both
test fakes were kinder than the real client (they deliver events at
`wait_seconds=0`; the real client returns before touching the transport).
Every remediation lands as: (1) make the fake reproduce the real semantics,
(2) pin the reviewer's exact trace as a failing regression test, (3) fix,
(4) prove. A fix proven against a forgiving fake is not proven.

## Task order

### H1 — P0-1: the pump must actually read the socket
- Unit fake + `ServerSim.receive_event` return `None` when
  `wait_seconds <= 0` (real-client semantics). Watch the e2e suite go red —
  that redness is the finding.
- `OrderEventPump.pump()` gets a positive default first-wait
  (`max_wait_seconds=0.25`) and REJECTS zero (a zero-wait pump is a no-op
  lie); the composed maintenance passes the explicit wait. Pin: after one
  pump against the faithful fake, a queued fill IS delivered and the
  protective stop IS submitted.

### H2 — P0-2A/B/C: single-close invariant under interleavings
- A: `_ingest_cancel` becomes idempotent on terminal orders — a cancel event
  for an already-`canceled`/`filled` order confirms journal state at most
  once and otherwise no-ops; it must never reach the emergency branch.
  Pin the duplicate-`Canceled(102)` trace.
- B: strategy exits are refused while `_pending_flatten` is set (the flatten
  owns the close; the exit intent is dropped with a `Rejected` event), and
  `_request_cancel_or_halt` reuses an already-requested cancel instead of
  re-POSTing (no journal-intent overwrite). Pin the same-bar
  backstop+strategy-exit trace: exactly one cancel POST, one intent record.
- C: the exit-rejection emergency suppression narrows from "any pending
  flatten" to "the rejected order IS the pending flatten's own liquidation".
  A strategy-exit rejection while a flatten awaits that exit's cancel must
  emergency-liquidate. Pin the Rejected-before-Canceled trace.

### H3 — P0-3: flatten gets a driver that outlives bars
- `run_flatten_rundown(broker, pump, timeout)` in `order_runner` (same shape
  as `run_startup_flatten`): pumps until `flatten_in_progress` clears or
  halts. Called (a) by LiveLoop's owner after `run()` returns with
  `broker.flatten_in_progress`, (b) on the data-outage path after the
  flatten request. Composition helper + tests; LiveLoop itself stays
  broker-agnostic.
- Liquidation rejection/failure clears `_liquidation_in_flight` and clears
  `_pending_flatten` before latching, so a later explicit operator retry is
  possible; the halt still stands. Pin the `failureReason=market_closed`
  trace including a successful second `flatten()` after the latch is
  operator-cleared by fresh hydration.

### H4 — P0-4: startup flatten's supported state space is enforced
- `startup_flatten` REFUSES (latch + raise, manual-flatten message) any
  inherited position with `quantity != 1` and any inherited working order
  whose side would INCREASE inherited exposure (or any order when the
  snapshot position is None and the order is not reducing) — the deferred
  partial lifecycle boundary, now enforced at the entry point instead of
  discovered mid-race.
- `_ingest_fill` for `ROLE_INHERITED` while locally flat: adopt the fill
  into `_position` FIRST, then `_emergency_flatten()` and raise — the real
  account's new exposure gets a liquidation instead of being ignored.
  Pin the inherited-Buy-fill-wins-the-race trace.

### H5 — P1-3: no stale state after successful resolution
- `_resolve_pending_flatten` clears `_pending_exit` when the flatten
  consumed its stop (pin: next entry is not rejected `position_already_open`
  after a clean resolve).
- Hydration accepts `Filled` as a terminal outcome for an accepted cancel
  (a cancel raced and lost to a fill is legitimately terminal). Pin the
  stop-wins-race-then-rehydrate trace end-to-end.

### H6 — P1-2: veto reference contract narrowed
- Live: an entry intent WITHOUT `signal_price` is malformed —
  `TradovateOrderSafetyError` (loud, code-bug class), same tier as missing
  stop metadata. No fallback, no divergence. Sim unchanged. Decision-record
  language corrected from "identical" to "identical over the enforced
  contract".

### H7 — P1-1: heartbeat/liveness ownership (design decision recorded)
- Short-term (this slice): `LiveBarSource`'s wait loop gains an optional
  `between_polls` callback wired to `pump.pump(0.25)` in the order
  composition, bounding maintenance cadence to the poll interval rather
  than the bar interval; the pump reads `last_transport_activity` and
  raises on liveness expiry (same 7.5s rule as the D2 runtime).
- The precise vendor socket-timeout threshold remains unverifiable offline
  (P1-01); record that explicitly — the demo envelope capture measures it.

### H8 — P2-2: CI runs the anchor evidence
- Commit the 9-month anchor CSV and the operator TV export as repository
  fixtures (private repo; ~17MB acceptable) and add a third CI job running
  the five operator-gated test files with the env vars pointed at them.
  The review's mutation proof (suppressing the 14:39 signal preceding the
  pinned 2025-11-27 anchor entry) becomes the canary: that exact mutation
  must turn CI red.

### H9 — P2-1: reachability + fake fidelity + doc corrections
- `order_runner.main()` gains `--compose-check`: builds the full session
  against in-process sentinels (no network) so the composition root is
  source-wired and executable; the Gate 5 literals stay pinned.
- `ServerSim` fidelity knobs: zero-wait faithfulness (H1), optional
  duplicate/reordered/delayed delivery, stop-trigger-by-price, partial and
  rejected liquidation responses — used by the H2-H4 pins.
- Decision records for #30-#33 get dated CORRECTION sections (never edit
  the original text): each overstated claim restated with its actual scope.

### H10 — P2-3: ODR evaluator prospective correction (research side)
- Per the v3 verdict's own directive: reconciliation checks recompute
  decisive-close/extension/geometry from `bar_by_timestamp` instead of
  metadata-vs-metadata; the global reverse-exit lookup keys by decision id.
  Sealed verdicts remain sealed; this is forward-only.

## Sequencing and evidence

H1 → H2 → H3 → H4 → H5 → H6 (broker-safety PR, one commit per task,
suite + anchor green at each) then H7 → H8 → H9 (infrastructure/evidence
PR) then H10 (research PR). Every reviewer trace becomes a permanent
regression test named `test_review_2026_07_19_<finding>`. HANDOFF §5/§6
updated only AFTER the fixes merge, with claims scoped to what the tests
actually prove — the review's central lesson.
