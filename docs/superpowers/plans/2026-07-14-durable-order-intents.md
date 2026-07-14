# Durable Order Intents Implementation Plan

Date: 2026-07-14
Status: in progress

## Task 1: Journal executable specification

Create `tests/test_order_intent_journal.py` first. Pin fresh creation, durable
pending/transition records, reopen, unresolved-state recovery, torn-tail
truncation, middle corruption rejection, hash tampering, illegal transitions,
and run-ID mismatch.

## Task 2: Journal implementation

Create `src/full_python/execution/order_intent_journal.py` with:

- `IntentState`, `IntentRecord`, and `IntentJournalError`;
- `OrderIntentJournal(path, run_id)`;
- `begin(...)`, `transition(...)`, `unresolved_intents`, and `close()`;
- canonical request digest and hash-chain helpers;
- `flush + os.fsync` on every append;
- verified-prefix recovery for only a torn final record.

## Task 3: Broker submission protocol tests

Add a recording journal fake and REST probes to broker tests. Require:

- pending exists before entry, stop, exit, cancel, and liquidation REST calls;
- acknowledged order IDs are durable for successful order submissions;
- explicit entry failure becomes `REJECTED`;
- transport/malformed outcomes become `SUBMISSION_UNKNOWN`;
- unknown outcome cannot retry;
- preexisting unresolved journal starts the broker recovery-latched.

## Task 4: Tradovate integration

Inject a journal into `TradovateBroker`. Require it for any flatten-capable
configuration. Replace direct mutating REST calls with journal-aware wrappers,
without changing fill/state semantics. Do not add an order-capable production
composition root.

## Task 5: Verify and record

Run focused journal/broker/live tests, full offline tests, and the baseline-
backed suite. Update `HANDOFF.md` and add a decision record that explicitly
leaves cross-restart broker reconciliation open.
