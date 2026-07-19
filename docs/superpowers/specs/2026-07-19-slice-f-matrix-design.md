# Slice F: end-to-end failure matrix + offline-list closure — design and tasks

Date: 2026-07-19
Audit findings: the failure-matrix "PARTLY VERIFIED — components pass;
**nothing calls them**" gap (the composition-level half of P1-6), **P3-4**
(observe runner's `accounts[0]`), **P2-5** (rollover halts on cancels never
confirmed). Parent: `2026-07-14-broker-safe-execution-design.md` § Slice F.

## F1 — observe runner explicit account selection (P3-4 completion)

`select_observe_account(accounts, account_id=?, account_spec=?)`:
explicit `TRADOVATE_ACCOUNT_ID`/`TRADOVATE_ACCOUNT_SPEC` always win and are
verified via the order runner's `require_account`; with no explicit
selection, exactly ONE visible account is unambiguous and used; multiple
accounts without explicit selection refuse with the visible list. The
single-account demo flow in `docs/live-observe-runbook.md` is unchanged.

## F2 — pump failures route into the ledgered invariant halt

`build_order_session`'s maintenance wrapper converts any pump/broker
exception into `ExecutionInvariantError`, so a composition-level failure
halts through LiveLoop's existing path: durable `execution_halt` ledger
entry, halt WITHOUT flatten (position state unknown — guardrail 5's
invariant-halt arm). The pump itself stays typed-transparent (its unit
contract is unchanged); `run_startup_flatten` runs pre-LiveLoop and keeps
raw typed errors for the runner's top-level handler.

## F3 — adversarial matrix, end-to-end through the real composition

New `tests/test_failure_matrix_e2e.py`: every scenario drives
`build_order_session` itself — schema-strict fake REST (validates order/
cancel/liquidation body shapes, auto-queues protocol-faithful user-sync
props events for fills/cancels/liquidations), scripted user-sync websocket,
a bar source that invokes the maintenance hook between bars exactly like
`bars_until`, and scripted strategies. Rows proven at composition level
(audit numbering): 5/6-class happy round trip with exactly-once feedback;
4+18 staged DLL flatten ending NORMAL with `daily_limit` veto; 16
market-closed veto with zero REST calls; 17 early-close backstop on a real
calendar date; 12 unknown-order fill → ledgered invariant halt; 14 REST
position drift via the pump's reconciliation interval → ledgered invariant
halt; 15 startup flatten driven by `run_startup_flatten` through the pump
then reopened by fresh journal-correlated hydration. Row 19 (P2-5) is
pinned at broker level: a CONFIRMED cancel crosses session rollover clean;
an unconfirmed one still halts (fail-closed by design — with the pump
delivering confirmations, chronic false halts are gone).

## Partial quantities — explicit boundary, deferred lifecycle

The 1-lot boundary is already enforced fail-closed three ways (loud
`quantity must equal 1` guard, `invalid_quantity` veto, halt on any
`partial_fill` event) and the pilot is flat-1-MNQ by decision. Full
multi-contract partial lifecycle (cumulative fills, residual protection) is
REQUIRED before AM-sized live trading (max 4) and is deliberately deferred
until after the pilot; recorded as the one remaining Slice F item.

## Tasks

1. F1 + tests (pure selection function).
2. F2 + composition test (pump error → invariant halt).
3. F3 matrix suite; P2-5 broker pins.
4. Decision record + HANDOFF §5/§6 (offline list closed except deferred
   partial lifecycle); both suites green; PR.
