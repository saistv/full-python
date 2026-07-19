# Order-event pump and broker risk veto (Slice G) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-07-19
Design: `docs/superpowers/specs/2026-07-19-order-pump-and-veto-design.md`

Global constraints: stdlib-only, Python 3.9 floor, TDD, one commit per task,
offline fakes only, sim/paper identity untouched, live flags default-False.

## Task G1: Broker risk veto (P1-7)

Files: `src/full_python/tradovate/broker.py`, `tests/test_tradovate_broker.py`.

- [x] Failing tests: (a) `order_enabled` without `risk_limits` raises
  `TradovateConfigError`; (b) an entry intent on a session the calendar
  closes (`rth_close_minutes_et=None`) is `Rejected(reason="market_closed")`
  with `rest.placed == []` and no journal record; (c) an entry inside the
  after-flatten window is `Rejected(reason="after_flatten")` (exact sim
  reason string from `risk/session_rules.py`); (d) a buy intent whose
  `stop_price >= signal_price` is `Rejected(reason="invalid_stop")` before
  any POST; (e) a valid intent still places (existing tests keep passing
  with `_cfg` growing a default `RiskLimits(max_contracts=1,
  flatten_minutes_et=959, rth_entries_only=True)`).
- [x] Implement: constructor requirement + `self._risk_manager =
  RiskManager(risk_limits)` when provided; veto evaluation at the top of the
  entry-intent branch in `apply_strategy_result`; veto → `Rejected` event +
  `continue`.
- [x] Full broker file green; commit.

## Task G2: order-event translation (pure)

Files: `src/full_python/tradovate/order_events.py` (new),
`tests/test_tradovate_order_events.py` (new).

- [x] Failing tests: fill Created → one fill raw event with mapped fields;
  order Canceled → cancel; order Rejected → reject with reason; order
  Working/Filled → []; position entity → position raw event with
  netPos→side/qty mapping (1→long/1, -2→short/2, 0→flat/0); non-order
  entity types (cashBalance, accountRiskStatus, command, commandReport,
  orderVersion, account, contract) → []; foreign accountId or contractId →
  `TradovateStateError`; absent identity → injected from scope; malformed
  fill (missing qty/price/orderId) → raises; non-props / shutdown /
  non-dict events → raises.
- [x] Implement `translate_user_sync_event(event, *, account_id,
  contract_id) -> list[TradovateRawEvent]` per spec §G2.
- [x] Green; commit.

## Task G3: OrderEventPump (P1-6)

Files: `src/full_python/tradovate/order_pump.py` (new),
`tests/test_tradovate_order_pump.py` (new).

- [x] Failing tests with fakes (FakeWebSocket with scripted events +
  `last_transport_activity`, FakeBroker recording `ingest_raw_event` /
  `reconcile_rest_positions`, ManualClock): (a) pump translates and delivers
  scripted fill+cancel events in order and returns the count; (b) heartbeat
  sent when due (2.5s cadence), not before; (c) reconciliation interval
  triggers `rest.position_list` → `broker.reconcile_rest_positions` and
  re-arms; (d) shutdown frame raises and nothing is delivered after it;
  (e) translator or broker exceptions propagate (nothing swallowed);
  (f) constructor validates intervals positive/finite; (g) a props event for
  a non-lifecycle entity delivers nothing but still counts as transport
  activity (no liveness regression).
- [x] Implement `OrderEventPump` per spec §G3.
- [x] Green; commit.

## Task G4: order-runner composition skeleton

Files: `src/full_python/live/order_runner.py` (new),
`tests/test_order_runner.py` (new).

- [x] Failing tests: `build_order_session` composes broker (with
  `risk_limits`), pump-in-maintenance, and LiveLoop from injected fakes; the
  maintenance hook invokes `pump.pump`; explicit account selection is
  required and a mismatch raises (no `accounts[0]` fallback — P3-4 not
  repeated); the CLI `main()` pins `order_enabled=False` /
  `flatten_enabled=False` literals (test asserts by inspection of the built
  config, not by parsing source).
- [x] Implement per spec §G4; `main()` carries the Gate-5 comment.
- [x] Green; commit.

## Task G5: docs

- [x] Decision record `docs/decisions/2026-07-19-order-pump-and-veto.md`
  (findings closed in code: P1-7 fully, P1-6's caller with the runtime
  division-of-authority note; still open: P1-01 envelope, P1-8, Slice F,
  P3-4 closed in the NEW runner only).
- [x] HANDOFF §5 bullet + §6 task-1 scope update.
- [x] Full suite + anchor suite green; commit; PR.
