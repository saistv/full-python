# Account-State Hydration Implementation Plan

Date: 2026-07-14
Design: `docs/superpowers/specs/2026-07-14-account-state-hydration-design.md`

## Task 1: Broker-visible logical correlation

- Extend journal records with a hash-covered client operation ID while
  preserving verified schema-1 reads.
- Generate the ID before each POST and include it in the digested request.
- Send `clOrdId` on order/cancel and `customTag50` on liquidation.
- Test durability, tamper detection, length/uniqueness, and pre-POST ordering.

## Task 2: Read-only account authority

- Add REST list helpers for orders, commands, order versions, cash balances,
  and account risk status.
- Add a protocol-faithful initial user-sync request helper.
- Build strict normalization for exact account and contract identity.
- Compare user-sync and REST safety state without order-dependent list order.
- Bind cash-balance realized P&L to an explicit expected `tradeDate`.

## Task 3: Broker hydration gate

- Start all order-capable brokers recovery-latched.
- Add one atomic stable-flat hydration operation.
- Require safe account flags, normal risk, exact identity, zero position, zero
  working/transitional orders, and no unresolved journal intents.
- Carry account daily realized P&L into the broker risk context.
- Reconcile acknowledged history only through exact automated command ID and
  terminal broker order identity. Reconcile an accepted cancel only through
  its exact command and canceled/expired order; keep pending, unknown, legacy,
  and mismatched history closed.
- Keep inherited positions/orders closed and report the exact reason.

## Task 4: Adversarial verification and records

- Test missing/duplicate/foreign/malformed entities, cross-contract exposure,
  source disagreement, unsafe risk, stale journal history, and repeated
  hydration.
- Run focused, full offline, and baseline-backed suites.
- Review the diff against P0-05/P1-01/P1-02 without overstating closure.
- Update the handoff and add a dated decision record.
