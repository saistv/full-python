# Durable Order Intent Journal Design

Date: 2026-07-14
Status: implementation in progress
Parent: `docs/superpowers/specs/2026-07-14-broker-safe-execution-design.md`
Audit targets: P0-05 and the financial-recovery portion of P2-05

## Problem

A Tradovate POST can be accepted even when its HTTP response is lost. The
current broker records an order only after receiving an `orderId`, so a timeout
can leave an entry, stop, exit, cancellation, or liquidation at the broker with
no durable local identity. Retrying can duplicate or reverse exposure.

The live event ledger is an observational report. It flushes but does not
`fsync`, refuses restart recovery, and has no causal order identity. It must not
be promoted into financial authority by implication.

## Boundary

Add a separate `OrderIntentJournal` for broker-mutating POSTs. Every submission
must write a durable `SUBMISSION_PENDING` record before the REST call. The
result then appends exactly one of:

- `ACKNOWLEDGED` with broker order ID when supplied;
- `REQUEST_ACCEPTED` for cancel requests whose HTTP response has no order ID;
- `REJECTED` for an explicit broker failure response;
- `SUBMISSION_UNKNOWN` for a transport error or malformed response.

`SUBMISSION_PENDING` and `SUBMISSION_UNKNOWN` are unresolved states. Because
volatile order state is lost on process restart, opening a broker with any
preexisting journal history latches recovery, including acknowledged intents.
This slice never guesses or retries. Slice D must hydrate and match journal
history to account-scoped broker orders/fills before releasing that latch.

## Durable Record

Each JSONL record contains:

- schema version and run ID;
- monotonic journal sequence;
- logical intent ID and operation role;
- configured account ID and contract ID;
- canonical SHA-256 request-body digest;
- lifecycle state;
- optional broker order ID and bounded diagnostic detail;
- previous-record hash and current-record hash.

The request body itself is not stored. Account and contract identity plus the
digest are enough to prove which canonical request was submitted without
creating another location for sensitive payload data.

Every append writes one complete line, flushes, and calls `os.fsync`. A restart
verifies sequence, run ID, hash chain, state transitions, and intent identity.
A malformed final line is treated as a torn write and truncated to the last
verified byte. Corruption before the final line fails closed.

## Broker Integration

- Flatten-capable brokers require a journal dependency. Observe mode remains
  order-disabled/flatten-disabled and needs no journal.
- One wrapper owns begin-before-POST and result transition ordering.
- Entry, protective stop, strategy exit, and liquidation submissions journal
  the accepted broker order ID before registering it in volatile broker state.
- Cancel requests journal request acceptance; the asynchronous cancel event
  remains the only cancellation confirmation.
- Explicit entry rejection remains retryable only after its journal state is
  durable and the normal stable-flat rules pass.
- Any ambiguous outcome latches `RECOVERY_REQUIRED`; later strategy signals
  cannot create a new logical intent or REST call.

## Nonclaims

This journal does not discover orders, hydrate positions, confirm cancellation,
or prove liquidation completed. It does not by itself make restart order-safe.
Those require account user sync and REST reconciliation in Slice D. P0-05
remains open until unresolved intents are reconciled across restart against a
protocol-faithful broker.

## Acceptance

1. Every mutating POST has a pending journal record before the fake REST method
   is entered.
2. Timeout-after-acceptance leaves one unresolved intent and no retry.
3. A broker constructed from any nonempty prior journal starts recovery-latched.
4. Explicit rejection is durable and permits a later distinct entry intent.
5. Successful submissions persist broker order IDs before volatile mapping.
6. Torn final records recover the verified prefix; middle corruption fails.
7. Hash, sequence, run, state, account, contract, and body-digest tampering fail.
8. Observe mode still cannot touch broker REST or create financial intents.
