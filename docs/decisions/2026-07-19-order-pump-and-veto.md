# Order-event pump and broker risk veto (Slice G) — IMPLEMENTED OFFLINE

Date: 2026-07-19
Design: `docs/superpowers/specs/2026-07-19-order-pump-and-veto-design.md`
Plan: `docs/superpowers/plans/2026-07-19-order-pump-and-veto.md`
Audit: `docs/audits/2026-07-13-adversarial-audit.md` (P1-6, P1-7, P3-4)

## What changed

**P1-7 CLOSED — the broker now applies the exact veto the simulator applies.**
`TradovateBroker(order_enabled=True)` requires `risk_limits`; every entry
intent is evaluated by the shared `risk/risk_manager.py` module before any
journal or REST activity, with sim-identical reason strings
(`market_closed`, `after_flatten`, `outside_rth`, `position_already_open`,
`daily_limit`, `invalid_quantity`, `invalid_stop`). Failure-matrix row 16's
class is closed: on a day the calendar closes, live now vetoes
`market_closed` exactly as sim/paper do, before any POST. Malformed strategy
output (missing stop metadata, quantity != 1) deliberately stays a LOUD
`TradovateOrderSafetyError` — those are code bugs, not market conditions.
The veto also exposed and fixed an unrealistic short-entry test fixture
whose stop sat on the wrong side of the price.

**P1-6 CLOSED in code — the hardened lifecycle has a production caller.**
- `tradovate/order_events.py`: pure translation of user-sync props events
  into the broker's raw fill/cancel/reject/position events. Identity is
  verified when present and injected from the account-filtered scope when
  absent; a foreign identity, an unknown order status, or a mutated fill
  raises. Non-lifecycle entity types translate to nothing.
- `tradovate/order_pump.py`: `OrderEventPump.pump()` runs inside the
  bar-source maintenance hook (`bars_until`), draining available events into
  `broker.ingest_raw_event`, sending the application heartbeat on the
  runtime cadence, and feeding account-scoped REST position snapshots to
  `broker.reconcile_rest_positions` on a bounded interval. Every exception
  propagates into LiveLoop's halt handling.
- `live/order_runner.py`: `build_order_session` composes broker (with the
  veto) + pump-in-maintenance + LiveLoop. Division of authority per the
  design: the stable-flat D2 runtime remains the startup hydrator and
  flat-idle verifier; during a trade the broker is authoritative and the
  pump feeds its lifecycle; the pump never calls `hydrate_account_state`.

**P3-4 closed in the NEW runner** — account selection is explicit
(`require_account` verifies the configured id AND name against the
credential's account list; refuses to guess). The observe runner's
`accounts[0]` remains as-is until its own touch-up. **Gate 5 boundary:**
`main()`'s `build_gate5_config` pins `order_enabled=False` /
`flatten_enabled=False` as literals — no flag or environment variable can
flip them; they change only by editing source after demo observe → demo
order test → paper → reconciliation pass.

## Evidence

- New: 10 translation tests, 7 pump tests, 4 composition tests, 4 veto
  tests; 5 existing rejection assertions updated to the sim-identical
  reasons (`position_already_open`, `daily_limit`).
- Offline suite green at every task commit; final: see the PR body for the
  exact counts (offline and 9-month-anchor runs). Sim/paper identity
  untouched — no `PositionEngine`/`SimulationEngine`/strategy changes.

## Still open

P1-01 (real DEMO split-sync envelope — the translator and pump are built to
the documented shapes and fail closed on anything else; the first real
connection is the experiment), P1-8 (restart/inherited-position recovery),
Slice F (partial quantities + full adversarial failure matrix), P2-5
(rollover cancel confirmation), the observe runner's own `accounts[0]`,
and every attended Gate 5+ drill. Nothing may trade live.


---

## CORRECTION (2026-07-19, after the independent review — PR #35)

Two claims were overstated. (1) "P1-6 closed by a production caller": the
composed pump's default zero-second wait meant the real websocket client
returned before reading the transport — the pump performed no real socket
reads — and no source-wired runnable entry point invoked the composition.
Fixed in Slice H (H1: positive-wait contract, faithful fakes; H9:
`--compose-check` makes the root source-wired) — PRs #37/#38. (2) "the
exact veto the simulator applies": the missing-`signal_price` fallback used
different reference bars in sim and live. Fixed by contract in H6:
`signal_price` is required for live entries. Heartbeat cadence was also
bar-bound, not the documented 2.5s; H7 adds sub-bar maintenance slicing and
transport liveness. The precise vendor socket threshold remains a P1-01
demo-capture item.
