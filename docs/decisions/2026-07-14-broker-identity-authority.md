# Broker Identity Authority

**Decision:** close the unsafe position-netting portion of principal-audit
P1-02 in offline code by binding every order-capable Tradovate adapter to one
exact account and active contract. Use the documented contract-ID liquidation
request shape, but keep both findings open until runtime identity resolution,
order reconciliation, and broker-confirmed flat state are implemented.

Parent design:
`docs/superpowers/specs/2026-07-14-broker-safe-execution-design.md`.

## Failure Closed

The adapter previously carried only `root_symbol="NQ"`. Position snapshots
were summed without account or contract filtering, which could hide a roll
straddle or reconcile another account. Normal and emergency liquidation
inferred a symbol from the current bar and sent `accountSpec` and `symbol`
instead of an exact contract ID.

## Implemented Authority Model

- `root_symbol` remains the instrument-economics authority; `contract_symbol`
  and positive `contract_id` now identify the exact Tradovate contract.
- Every flatten-capable configuration requires both exact contract fields.
  Order-capable mode inherits that requirement because it requires flatten.
- Entry and exit decisions must name the exact configured contract before any
  REST action is allowed.
- Fill, partial-fill, and position events require matching `accountId` and
  `contractId`. Cancel and reject events remain scoped through known order IDs.
- REST position reconciliation accepts only an empty snapshot or one exact
  account/contract row. Missing identity, foreign rows, duplicate rows, and
  old/new-contract roll exposure halt instead of being netted.
- Normal and emergency liquidation send exactly `accountId`, `contractId`, and
  `admin`; current-bar symbols cannot choose the liquidation target.
- Order-disabled observe mode remains unchanged and cannot place or liquidate.

## Acceptance Evidence

- Exact front-contract entry and protection use `NQU6`; a root-symbol `NQ`
  intent is rejected before REST.
- Missing, malformed, foreign-account, and foreign-contract broker events halt.
- Duplicate and roll-straddle REST snapshots halt even when aggregate quantity
  would appear flat.
- Schema-strict fake liquidation rejects any field outside
  `accountId + contractId + admin`.
- Focused Tradovate configuration/broker/live-loop suite: 76 passed.
- Full offline suite: 442 passed, 4 data-gated skips.
- Full suite with `FULL_PYTHON_BASELINE_DATA`: 446 passed.

The baseline-backed suite proves this safety change did not alter the frozen
simulator/PaperBroker result. Fake transports prove local protocol intent, not
Tradovate acceptance or live broker parity.

## Remaining Blockers

P0-04 remains open because HTTP acceptance is not proof of flat position or
cleared working orders. P1-02 also remains open at the broader audit level:
there is no order-capable composition root that resolves and persists the
contract ID, and REST working-order snapshots are not reconciled yet. Those
parts join P1-01 startup hydration. P0-03 and P0-05 remain open as well:
scheduled/shutdown flatten and durable idempotent intent recovery. Partial
quantities and the complete adversarial failure matrix remain open.

The project remains **RESEARCH-ONLY**. No demo or funded orders are authorized.
