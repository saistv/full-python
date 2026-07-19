# Slice F + offline-list closure — IMPLEMENTED OFFLINE

Date: 2026-07-19
Design: `docs/superpowers/specs/2026-07-19-slice-f-matrix-design.md`
Audit: `docs/audits/2026-07-13-adversarial-audit.md`

## What closed

**The composition-level failure matrix.** The audit's core structural
complaint — "components pass; **nothing calls them**" — is answered by
`tests/test_failure_matrix_e2e.py`: seven matrix rows proven through the
REAL composed stack (`build_order_session` + OrderEventPump in the
maintenance hook + LiveLoop + the real intent journal + a schema-strict
fake server that validates order/cancel/liquidation body shapes and queues
protocol-faithful user-sync props):

| Audit row | Composition-level proof |
|---|---|
| 5/6-class | Entry → protective stop → strategy exit round trip; exactly-once on_fill/on_trade_closed |
| 4 + 18 | DLL breach runs the STAGED flatten via the pump; ends NORMAL; later entry vetoed `daily_limit` |
| 16 | Full-holiday session vetoes `market_closed` with zero REST calls |
| 17 | Real 2025-11-28 (13:15 early close): broker backstop flattens at close−1 |
| 12 | Unknown-order fill → ledgered `execution_halt` (invariant arm), no flatten |
| 14 | REST/fill-derived position drift via the pump's reconciliation interval → ledgered halt |
| 15 | Startup flatten via `run_startup_flatten` + pump → fresh journal-correlated hydration → clean session |

The suite immediately earned its keep, finding one real composition bug:
`run_startup_flatten` left the recovery's order events queued, so LiveLoop's
fresh order-state shadow replayed the startup liquidation fill as a phantom
short and halted on the cross-check. The recovery driver now drains its own
events. A second refinement: `build_order_session`'s maintenance wrapper
routes pump/broker failures into `ExecutionInvariantError`, so composition
failures halt through LiveLoop's durable `execution_halt` ledger path
WITHOUT flatten (guardrail 5's invariant arm) instead of escaping as raw
exceptions.

**P3-4 fully closed.** The observe runner's `accounts[0]` is replaced by
`select_observe_account`: explicit `TRADOVATE_ACCOUNT_ID`/`ACCOUNT_SPEC`
always win and are verified; a single visible account is unambiguous;
multiple accounts without explicit selection refuse with the visible list.
Runbook updated; the single-account demo flow is unchanged.

**P2-5 closed.** With the pump delivering cancel confirmations (P1-6), the
chronic false rollover halts are gone — pinned: a CONFIRMED cancel crosses
session rollover clean; an unconfirmed one still halts fail-closed (that
halt is correct: state genuinely unknown).

## Deliberately deferred (the one remaining Slice F item)

**Multi-contract partial-fill lifecycle** (cumulative fills, residual
protection). The 1-lot boundary is enforced fail-closed three ways (loud
`quantity must equal 1` guard, `invalid_quantity` veto, halt on any
partial-fill event) and the retained pilot is flat-1-MNQ by decision. The
full lifecycle is REQUIRED before AM-sized live trading (max 4 contracts)
and is parked until after the pilot.

## Evidence

Offline suite **779 passed / 5 skipped**; 9-month anchor
**783 passed / 1 skipped**; sim/paper identity untouched.

## Offline backlog status

CLOSED: P0-1..P0-4 (in-code legs), P1-3..P1-8, P2-3, P2-4, P2-5, P3-4,
matrix rows at composition level. REMAINING (all credential-gated or
post-pilot): P1-01 real DEMO split-sync envelope, P0-04 REST leg + attended
liquidation drill, P0-05 recovery association against real broker state,
the broader P1-02, P2-1/P2-2 (research characterization), P3-1..P3-3
(documentation/config notes), partial-fill lifecycle (post-pilot), and
every attended Gate 5+ drill. Nothing may trade live; the Gate 5 boundary
literals are unchanged.
