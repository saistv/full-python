# Broker Identity Authority Implementation Plan

Date: 2026-07-14
Status: in progress
Parent design: `docs/superpowers/specs/2026-07-14-broker-safe-execution-design.md`

## Scope

This slice makes every order-capable Tradovate adapter authoritative for one
configured account and one exact active contract. It is an offline prerequisite
for safe liquidation and later startup hydration. It does not claim that a
liquidation submission proves the account flat.

## Configuration Contract

- `root_symbol` identifies instrument economics (`NQ` or `MNQ`).
- `contract_symbol` identifies the exact Tradovate contract used for orders.
- `contract_id` identifies that same contract in position and liquidation APIs.
- `flatten_enabled=True` requires a nonblank contract symbol and positive
  contract ID. Because order-enabled mode already requires flatten capability,
  this covers every order-capable configuration.
- Observe mode remains order-disabled and flatten-disabled, so it may continue
  without order authority until its contract authority is connected to the
  broker adapter in a later composition-root slice.

## Required Behavior

1. Entry intents must name the configured exact contract symbol.
2. Position WebSocket events must contain the configured account ID and
   contract ID before their side and quantity can be trusted.
3. REST position snapshots may be empty or contain exactly one row for the
   configured account and contract. Missing identity, foreign identity, or
   duplicate rows halt reconciliation, including offsetting roll positions.
4. Fill and partial-fill events must contain matching account and contract
   identity. Cancel and reject events remain scoped through their known
   submitted order ID.
5. Normal and emergency liquidation use exactly `accountId`, `contractId`, and
   `admin`. They never infer the contract from the current bar.
6. Registered liquidation orders retain the configured contract symbol for
   subsequent fill accounting.

## Test Order

1. Add configuration failures for missing/invalid active identity.
2. Add exact-symbol order submission and wrong-symbol rejection tests.
3. Add WebSocket position/fill identity tests.
4. Add REST snapshot tests for exact match, missing identity, foreign account,
   foreign contract, duplicate contract rows, and roll straddles.
5. Replace permissive liquidation assertions with a schema-strict fake that
   accepts only the documented identity body.
6. Implement the smallest configuration and broker changes that pass them.
7. Run focused Tradovate tests, the full offline suite, and baseline-backed
   equivalence before publishing a pull request.

## Acceptance Boundary

Passing this slice closes P1-02's unsafe position-netting path and supplies the
valid request-schema prerequisite for P0-04. The broader P1-02 remains open
until the order-capable composition resolves/persists identity and reconciles
working-order snapshots during Slice D. P0-04 remains open until Slice E
confirms flat position and no working orders after every flatten path.
