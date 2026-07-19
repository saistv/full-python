# Order-event pump and broker risk veto (Slice G) — design

Date: 2026-07-19
Audit findings: **P1-6** (no account-event pump: `ingest_raw_event` /
`reconcile_rest_positions` have no production caller, so the entire hardened
order lifecycle is unreachable), **P1-7** (`TradovateBroker` applies no
RiskManager veto; it will submit orders the simulator refuses — failure-matrix
row 16: on a day the calendar closes, sim/paper veto `market_closed` while the
live broker submits).
Parent design: `docs/superpowers/specs/2026-07-14-broker-safe-execution-design.md`.

## G1 — shared risk veto in the broker (P1-7)

`risk/risk_manager.py` was extracted for exactly this ("any live
BrokerExecutionEngine calls this same module, never simulation-internal
code"). Wire it:

- `TradovateBroker.__init__` gains `risk_limits: Optional[RiskLimits]`;
  fail-closed constructor rule: **`order_enabled` requires `risk_limits`**
  (same style as the daily-loss/flatten requirements).
- In `apply_strategy_result`, for each entry intent, BEFORE any journal or
  REST activity: evaluate `RiskManager.veto_reason` with
  `has_open_order = position or working orders or pending exit or pending
  flatten or liquidation in flight`, the broker's `daily_limit_hit`, the
  bar's `session`, and `reference_price = float(intent.metadata.get(
  "signal_price", bar.close))`. A veto emits
  `Rejected(order_id="", reason=<veto>)` and skips the intent — byte-for-byte
  the same reason strings the simulator produces.
- The existing hard checks (stop-price metadata, quantity==1, stable-flat,
  hydration) remain after the veto as belt-and-suspenders.

## G2 — user-sync order-event translation (pure)

New `src/full_python/tradovate/order_events.py`:
`translate_user_sync_event(event, *, account_id, contract_id) ->
list[TradovateRawEvent]`. Pure function, no I/O:

- `props/fill/Created` → `kind="fill"` with
  `{orderId, action, qty, price, timestamp, accountId, contractId}`.
- `props/order/*` with `ordStatus="Canceled"` → `kind="cancel"`;
  `ordStatus="Rejected"` → `kind="reject"` (reason from the entity when
  present); `Working`/`Filled`/transitional statuses → no event (acks are
  known at submission; fills arrive as fill entities).
- `props/position/*` → `kind="position"` with `netPos` mapped to
  `side/qty` (`>0` long, `<0` short, `0` flat) in the shape
  `_reconcile_position_event` already consumes.
- Any other entity type → `[]` (account-cache concern, not order lifecycle).
- Identity is **verified when present, injected from scope when absent**: the
  D2 sync request is filtered to exactly one account, so entities that omit
  `accountId`/`contractId` inherit the configured scope; entities that carry
  a DIFFERENT identity raise `TradovateStateError` (fail closed). Malformed
  entities (missing orderId/qty/price where required, non-dict) raise.
- Duplicate delivery is not the translator's problem: the broker's own
  lifecycle halts on duplicate fills and tolerates duplicate cancels.

## G3 — OrderEventPump (P1-6 production caller)

New `src/full_python/tradovate/order_pump.py`, class `OrderEventPump`:

- Owns the authorized user-sync websocket client and the account-scoped REST
  client. Constructed with `broker`, `config`, `monotonic_clock`, and
  `reconciliation_interval_seconds` (default 30.0, validated positive/finite).
- `pump(max_wait_seconds: float) -> int`: drain available websocket events
  (bounded, non-blocking after the first wait): shutdown frames raise;
  props events go through `translate_user_sync_event` and each raw event is
  fed to `broker.ingest_raw_event`; returns the number of raw events
  delivered. Sends the `[]` application heartbeat on the same cadence rule
  as the account runtime (2.5s). On the reconciliation interval, calls
  `rest.position_list()` filtered to the configured account and feeds
  `broker.reconcile_rest_positions(...)` — the position-aware path, valid
  mid-trade.
- **Any exception propagates.** The pump runs inside the bar-source
  `maintenance` hook (`live/runner.py: bars_until`), so a raise surfaces
  through LiveLoop's existing halt handling and the durable
  `execution_halt` ledger entry. No swallowing.
- Division of authority: the stable-flat D2 `TradovateAccountSyncRuntime`
  remains the STARTUP hydrator and flat-idle verifier; DURING a trade the
  broker is authoritative (parent design § Slice A) and the pump feeds its
  hardened lifecycle. The pump never calls `hydrate_account_state`.

## G4 — order-runner composition root (skeleton)

`src/full_python/live/order_runner.py`: builds the full order-capable stack
offline-constructibly (fakes in tests): auth → startup hydration via the D1/D2
hydrator → broker with `risk_limits` → OrderEventPump in the `bars_until`
maintenance hook → LiveLoop. The CLI `main()` pins `order_enabled=False` and
`flatten_enabled=False` as literals with a Gate-5 comment — the runner exists
so the demo-order gate has a vehicle, but no flag can enable orders from the
command line until the gates pass. Explicit account selection is REQUIRED
(`TRADOVATE_ACCOUNT_ID`/`ACCOUNT_SPEC` must match a listed account) — the
observe runner's `accounts[0]` auto-selection (audit P3-4) is not repeated.

## Non-goals

No credentials or network claims (P1-01's real split-sync envelope stays
open); no partial quantities (Slice F); no change to P1-8 startup-with-
position behavior (still halts); no change to sim/paper identity.
