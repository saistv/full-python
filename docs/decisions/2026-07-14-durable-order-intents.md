# Durable Order Intents

**Decision:** require a durable, hash-linked logical intent before every
broker-mutating POST and prohibit blind retry after any ambiguous outcome.
Treat this as the persistence foundation for P0-05, not closure of the finding:
Slice D must still reconcile journal history to account-scoped broker truth.

Parent design:
`docs/superpowers/specs/2026-07-14-durable-order-intent-journal-design.md`.

## Failure Reduced

The Tradovate adapter previously created volatile order state only after an
HTTP response supplied an `orderId`. A request accepted by the broker followed
by a lost response became an undiscoverable local orphan, and retry could
duplicate entry or reverse exposure. The general live event log also claimed
crash safety despite using flush without `fsync`, causal IDs, or tail recovery.

## Implemented Recovery Boundary

- A separate `OrderIntentJournal` stores financial submission authority; the
  live event ledger is explicitly a best-effort observational trace.
- Every entry, protective stop, strategy exit, cancel, and liquidation writes
  `SUBMISSION_PENDING` and `fsync`s before REST is entered.
- Accepted order IDs become durable `ACKNOWLEDGED` records before volatile
  submitted-order mapping.
- Cancel HTTP acceptance remains unresolved until the asynchronous cancel event
  records `CONFIRMED`.
- Explicit failures become `REJECTED`; transport or malformed outcomes become
  `SUBMISSION_UNKNOWN` and latch `RECOVERY_REQUIRED`.
- Any preexisting journal history starts a new broker instance recovery-latched,
  even when the final record contains an acknowledged broker order ID. Volatile
  order/position state must be hydrated before that latch can ever reopen.
- Repeated entry, cancel, and liquidation requests cannot create duplicate REST
  submissions while their first lifecycle is working or uncertain.
- Journal records include schema/run/sequence, logical intent, account and
  contract IDs, canonical request digest, lifecycle state, causal hashes, and
  optional broker order ID. Request bodies are not persisted.
- Every append uses `flush + os.fsync`; a torn final line recovers the verified
  prefix, while complete invalid records, hash/sequence/run tampering, illegal
  transitions, and concurrent writers fail closed.

## Acceptance Evidence

- Focused journal/persistence/broker/live-loop suite: 88 passed.
- Full offline suite: 463 passed, 4 data-gated skips.
- Full suite with `FULL_PYTHON_BASELINE_DATA`: 467 passed.
- Fake REST boundary probes observe pending journal state before every POST.
- Timeout-after-acceptance and non-Tradovate exceptions remain one unresolved
  logical intent and cannot retry.
- Repeated flatten produces one liquidation, including after unknown outcome.

The anchored suite proves historical simulation behavior is unchanged. Fake
transports and a local journal do not prove restart reconciliation or Tradovate
idempotency.

## Remaining Blockers

P0-05 remains open until Slice D can query and synchronize account-scoped
orders/fills, associate them with pending/unknown/acknowledged journal intents,
and prove exactly one broker outcome across restart. P1-01 and the remaining
P1-02 order-identity work therefore remain direct dependencies. P2-05 also
remains open for general replay/checkpoint claims; its inaccurate event-log
crash-safety claim is removed, and only the financial intent journal now has a
durability contract.

No order-capable composition root was added. The project remains
**RESEARCH-ONLY** and no demo or funded order is authorized.
