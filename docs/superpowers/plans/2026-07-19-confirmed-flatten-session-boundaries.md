# Slice E: Confirmed Flatten and Session Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-07-19
Design: `docs/superpowers/specs/2026-07-14-broker-safe-execution-design.md` (§ Slice E)
Audit findings closed in code: **P0-2** (flatten does not await cancel confirmation), **P0-04** (flat + no-working-orders never confirmed after liquidation), **P1-5** (RECOVERY_REQUIRED latched by routine flatten, read by nothing), **P0-03** (backstop not driven by the exchange calendar / early closes).

**Goal:** `TradovateBroker.flatten()` becomes a staged, event-confirmed protocol — cancel working orders, wait for confirmed cancellation, only then liquidate, then confirm flat with no working orders by a one-bar deadline — and the broker itself triggers that protocol at exchange-calendar close minus one minute, including early closes.

**Architecture:** Reuse the discipline the strategy-exit path already has (`EXIT_PENDING_CANCEL` → confirmed cancel → submit market order) for the flatten path, held in a new `PendingFlatten` record progressed by `_ingest_cancel` / `_on_exit_fill` and deadlined by `process_bar_open`. Routine confirmed flattens end in `NORMAL` (entries stay blocked by the existing `_daily_limit_hit` gate); only genuinely unresolved outcomes latch `RECOVERY_REQUIRED`, which hydration already consumes. The session-close backstop reads `SessionInfo.rth_close_minutes_et` (exchange-calendar authority landed with PR #25) so early-close days flatten at close−1, not at a 15:59 that never comes.

**Tech stack:** stdlib-only Python (3.9 floor), pytest. No new dependencies.

## Global Constraints

- Python 3.9 compatible; `from __future__ import annotations` for builtin-generic annotations (broker.py already has it).
- Stdlib only. TDD: every behavior lands with its failing test first. Frequent commits, one per task.
- Offline evidence only: fakes, no credentials, no network. `order_enabled`/`flatten_enabled` remain default-False.
- Simulation/PaperBroker identity must remain untouched: no `PositionEngine`, `SimulationEngine`, or strategy changes in this slice.
- Do not weaken guardrail 5 (halt-and-flatten) or the emergency-flatten path's latch semantics.
- Full suite green before the docs task; run `python3 -m pytest -q` (expect the current pass count plus this slice's new tests; zero failures).

## File Structure

- Modify: `src/full_python/tradovate/broker.py` — all behavior changes live here (states, `PendingFlatten`, `flatten()`, `_ingest_cancel`, `_ingest_reject`, `_on_exit_fill`, `process_bar_open`).
- Create: `tests/test_tradovate_flatten_protocol.py` — self-contained per house style (each broker test file carries its own fakes; copy the minimal helpers named below from `tests/test_tradovate_broker.py` rather than importing across test files).
- Create: `docs/decisions/2026-07-19-confirmed-flatten-session-boundaries.md` (Task 5).
- Modify: `HANDOFF.md` §5/§6 (Task 5).

Test-file helpers to copy verbatim from `tests/test_tradovate_broker.py`: `RecordingIntentJournal`, `FakeRestClient` (includes `order_place`, `order_cancel`, and the liquidation endpoint used by `_assert_liquidation_request`), `_cfg`, `_new_broker`, `_flat_hydration_snapshot`, `_bar`, `_session`, `_entry_result`, `_fill_event`, `_entered_broker`. Add one local helper:

```python
def _cancel_event(order_id):
    return TradovateRawEvent(kind="cancel", data={
        "orderId": order_id,
        "accountId": 456,
        "contractId": 789,
    })
```

(Match the identity fields `_fill_event` carries in the copied helper; `_require_event_identity` must accept the cancel the same way existing cancel tests build it — check the existing `test_...cancel...` cases in `tests/test_tradovate_broker.py` and reuse their event shape exactly.)

---

### Task 1: PendingFlatten — flatten requests cancels and stops

**Files:**
- Modify: `src/full_python/tradovate/broker.py` (enum at ~93, dataclasses at ~114, `__init__` at ~157, `flatten()` at ~502)
- Test: `tests/test_tradovate_flatten_protocol.py`

**Interfaces:**
- Produces: `BrokerExecutionState.FLATTEN_PENDING_CANCEL`, `BrokerExecutionState.FLATTEN_PENDING_FILL`, `PendingFlatten(reason: str, awaiting_cancel_ids: frozenset[str], requested_on_bar: str)`, broker attribute `_pending_flatten: Optional[PendingFlatten]`, and `flatten()` that never submits a liquidation while a cancel is unconfirmed.
- Consumes: existing `_journaled_cancel`, `_requested_cancel_ids`, `_has_working_orders`, `_journaled_liquidation`, `_register_order`, `ROLE_EXIT`.

- [ ] **Step 1: Write the failing tests**

```python
def test_flatten_with_working_stop_requests_cancel_and_defers_liquidation():
    broker, rest = _entered_broker()  # long 1 with working protective stop 102
    bar = _bar()

    broker.flatten(bar, "daily_limit")

    # Cancel requested for the stop; liquidation NOT submitted yet.
    assert [b for b in rest.cancel_bodies] and not rest.liquidation_bodies
    assert broker.execution_state == BrokerExecutionState.FLATTEN_PENDING_CANCEL
    assert broker._recovery_required is False  # routine flatten does not latch


def test_flatten_while_flat_with_no_working_orders_is_a_noop():
    broker, rest = _hydrated_order_broker()  # order-capable, hydrated, flat
    state_before = broker.execution_state

    broker.flatten(_bar(), "daily_limit")

    assert not rest.cancel_bodies and not rest.liquidation_bodies
    assert broker.execution_state == state_before


def test_flatten_is_idempotent_while_pending():
    broker, rest = _entered_broker()
    broker.flatten(_bar(), "daily_limit")
    cancels = len(rest.cancel_bodies)

    broker.flatten(_bar(), "daily_limit")

    assert len(rest.cancel_bodies) == cancels
    assert not rest.liquidation_bodies


def test_flatten_cancel_failure_halts_and_keeps_stop_protection():
    broker, rest = _entered_broker()
    rest.fail_cancel = True  # FakeRestClient raises TradovateError on cancel

    with pytest.raises(TradovateStateError, match="cancel"):
        broker.flatten(_bar(), "daily_limit")

    assert not rest.liquidation_bodies          # nothing was liquidated blind
    assert broker._working_stop_id is not None  # stop still protects
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_flatten_with_position_but_no_working_orders_liquidates_immediately():
    broker, rest = _entered_broker_without_stop()  # defensive: stop already gone
    broker.flatten(_bar(), "daily_limit")

    assert rest.liquidation_bodies
    assert broker.execution_state == BrokerExecutionState.FLATTEN_PENDING_FILL
```

Helper `_hydrated_order_broker` = `_new_broker(_cfg(order_enabled=True, flatten_enabled=True), ...)` + `hydrate_account_state(_flat_hydration_snapshot())`. Helper `_entered_broker_without_stop` = `_entered_broker()` then simulate the stop already canceled through `_cancel_event` on the stop id with `_pending_flatten` unset — if that path raises by design (unexpected stop cancel triggers emergency flatten), instead build the state directly: `broker._orders.pop(stop_id)`, `broker._working_stop_id = None`. Keep whichever construction the existing suite uses for "unprotected position" cases (`grep "unprotected" tests/test_tradovate_broker.py`).

- [ ] **Step 2: Run and verify failure** — `python3 -m pytest tests/test_tradovate_flatten_protocol.py -q` → fails: `FLATTEN_PENDING_CANCEL` not defined.

- [ ] **Step 3: Implement**

Enum + dataclass:

```python
class BrokerExecutionState(str, Enum):
    NORMAL = "normal"
    ENTRY_PENDING_FILL = "entry_pending_fill"
    EXIT_PENDING_CANCEL = "exit_pending_cancel"
    EXIT_PENDING_FILL = "exit_pending_fill"
    FLATTEN_PENDING_CANCEL = "flatten_pending_cancel"
    FLATTEN_PENDING_FILL = "flatten_pending_fill"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True)
class PendingFlatten:
    reason: str
    awaiting_cancel_ids: frozenset
    requested_on_bar: str  # bar.timestamp_utc of the request, for the deadline
```

`__init__`: add `self._pending_flatten: Optional[PendingFlatten] = None` next to `self._pending_exit`.

Replace `flatten()` (current body at ~502-537):

```python
def flatten(self, bar: MarketBar, reason: str) -> None:
    if not self._config.flatten_enabled:
        raise TradovateOrderSafetyError("flatten_disabled")
    if self._pending_flatten is not None or self._liquidation_in_flight:
        return
    working = [o for o in self._orders.values() if o.status == "working"]
    if self._position is None and not working:
        return  # routine no-op: nothing to cancel, nothing to close
    to_cancel = []
    for order in working:
        if order.order_id in self._requested_cancel_ids:
            continue
        try:
            self._journaled_cancel(order.order_id)
        except Exception as exc:
            # Two live closing orders must never coexist (P0-2). The working
            # orders still stand; halt for review instead of liquidating blind.
            self._latch_recovery()
            raise TradovateStateError(
                f"flatten could not cancel working order {order.order_id}; "
                "halting with existing protection in place"
            ) from exc
        self._requested_cancel_ids.add(order.order_id)
        to_cancel.append(order.order_id)
    if to_cancel:
        self._pending_flatten = PendingFlatten(
            reason=reason,
            awaiting_cancel_ids=frozenset(to_cancel),
            requested_on_bar=bar.timestamp_utc,
        )
        self._execution_state = BrokerExecutionState.FLATTEN_PENDING_CANCEL
        return
    # Position with no working orders: liquidate directly, still confirmed.
    self._pending_flatten = PendingFlatten(
        reason=reason,
        awaiting_cancel_ids=frozenset(),
        requested_on_bar=bar.timestamp_utc,
    )
    self._submit_flatten_liquidation()
```

`_submit_flatten_liquidation()` (new; factor the liquidation body out of the old `flatten()`):

```python
def _submit_flatten_liquidation(self) -> None:
    pending = self._pending_flatten
    if pending is None:
        return
    position = self._position
    if position is None:
        # The working orders were canceled and no position remains: resolved.
        self._resolve_pending_flatten()
        return
    body = {
        "accountId": self._config.account_id,
        "contractId": self._active_contract_id(),
        "admin": False,
        "isAutomated": True,
    }
    self._liquidation_in_flight = True
    try:
        order_id, logical_intent_id = self._journaled_liquidation(body)
    except TradovateStateError:
        self._latch_recovery()
        raise
    except Exception as exc:
        self._latch_recovery()
        raise TradovateStateError(
            "liquidation submission outcome unknown; broker reconciliation required"
        ) from exc
    self._register_order(SubmittedOrder(
        order_id=order_id,
        role=ROLE_EXIT,
        side="sell" if position.side == "long" else "buy",
        quantity=position.quantity,
        symbol=self._active_contract_symbol(),
        reason=pending.reason,
        logical_intent_id=logical_intent_id,
    ))
    self._execution_state = BrokerExecutionState.FLATTEN_PENDING_FILL
    self._events.append(Acked(order_id=order_id))
```

`_resolve_pending_flatten()` is defined in Task 3 — for this task stub it as the two lines `self._pending_flatten = None` / `self._liquidation_in_flight = False` plus setting `NORMAL` when `not self._recovery_required`, and let Task 3's tests harden it (P0-04 verification).

The routine path no longer sets `_recovery_required = True` — that is the P1-5 fix; `_emergency_flatten` (~676) keeps its latch untouched.

- [ ] **Step 4: Run** — task tests pass; then the full broker file: `python3 -m pytest tests/test_tradovate_broker.py tests/test_tradovate_flatten_protocol.py -q`. Existing DLL-flatten tests in `test_tradovate_broker.py` that asserted the OLD one-shot behavior (immediate liquidation, recovery latch) will fail — update each to the staged protocol (cancel first, liquidation only after the cancel event, no recovery latch on the routine path). Every such edit is a deliberate behavior change of this slice; keep the assertion intent (safety) and change only the mechanics.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "flatten requests confirmed cancels before any liquidation (P0-2 first half)"`

---

### Task 2: Cancel confirmation progresses the flatten; the stop-fill race never double-closes

**Files:**
- Modify: `src/full_python/tradovate/broker.py` (`_ingest_cancel` at ~841, `_ingest_reject` at ~817)
- Test: `tests/test_tradovate_flatten_protocol.py`

**Interfaces:**
- Consumes: Task 1's `PendingFlatten`, `_submit_flatten_liquidation`, states.
- Produces: `_ingest_cancel` branch that removes confirmed ids from `awaiting_cancel_ids` and calls `_submit_flatten_liquidation()` when the set empties; `_ingest_reject` treatment of a flatten liquidation rejection (latch + raise, no emergency re-liquidation).

- [ ] **Step 1: Write the failing tests**

```python
def test_confirmed_cancel_then_liquidation_then_flat_daily_limit_sequence():
    broker, rest = _entered_broker()
    stop_id = broker._working_stop_id
    broker.flatten(_bar(), "daily_limit")

    broker.ingest_raw_event(_cancel_event(stop_id))
    assert rest.liquidation_bodies                     # submitted only now
    assert broker.execution_state == BrokerExecutionState.FLATTEN_PENDING_FILL

    liq_id = rest.last_liquidation_order_id
    broker.ingest_raw_event(_fill_event(liq_id, action="Sell", price=99.0))
    assert broker._position is None
    assert broker._pending_flatten is None
    assert broker.execution_state == BrokerExecutionState.NORMAL
    trades = broker.poll_strategy_feedback()
    assert len(trades) == 1 and trades[0].reason == "daily_limit"


def test_stop_fill_during_pending_cancel_resolves_flatten_without_liquidation():
    # P0-2's exact race: the stop fills before the cancel lands.
    broker, rest = _entered_broker()
    stop_id = broker._working_stop_id
    broker.flatten(_bar(), "daily_limit")

    broker.ingest_raw_event(_fill_event(stop_id, action="Sell", price=98.0))

    assert not rest.liquidation_bodies      # never submitted: no double close
    assert broker._position is None
    assert broker._pending_flatten is None
    assert broker.execution_state == BrokerExecutionState.NORMAL


def test_flatten_liquidation_rejection_latches_without_second_liquidation():
    broker, rest = _entered_broker()
    stop_id = broker._working_stop_id
    broker.flatten(_bar(), "daily_limit")
    broker.ingest_raw_event(_cancel_event(stop_id))
    liq_id = rest.last_liquidation_order_id
    liq_count = len(rest.liquidation_bodies)

    with pytest.raises(TradovateStateError):
        broker.ingest_raw_event(TradovateRawEvent(kind="reject", data={
            "orderId": liq_id, "reason": "liquidation rejected",
        }))

    assert len(rest.liquidation_bodies) == liq_count   # no re-attempt
    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED
```

`FakeRestClient` additions if not already present in the copied helper: record `cancel_bodies`, `liquidation_bodies`, expose `last_liquidation_order_id`, and a `fail_cancel` switch — mirror how the existing file's fake records `order_place` bodies.

- [ ] **Step 2: Run and verify failure** — the first test fails at the liquidation-after-cancel assertion (old code liquidated immediately, new code not yet wired to `_ingest_cancel`).

- [ ] **Step 3: Implement**

In `_ingest_cancel` (~841), after the `order.status = "canceled"` / `Canceled` append, insert the flatten branch BEFORE the existing protective-stop pending-exit branch:

```python
        pending_flatten = self._pending_flatten
        if pending_flatten is not None and order.order_id in pending_flatten.awaiting_cancel_ids:
            remaining = pending_flatten.awaiting_cancel_ids - {order.order_id}
            self._pending_flatten = PendingFlatten(
                reason=pending_flatten.reason,
                awaiting_cancel_ids=remaining,
                requested_on_bar=pending_flatten.requested_on_bar,
            )
            if not remaining:
                self._submit_flatten_liquidation()
            return
```

In `_on_exit_fill` (~782), after `self._position = None`, resolve a pending flatten when the closing fill arrived from ANY of its orders (the canceled-too-late stop or the liquidation):

```python
        if self._pending_flatten is not None:
            self._resolve_pending_flatten()
```

(`_resolve_pending_flatten` from Task 1's stub already clears pending + `_liquidation_in_flight` and restores `NORMAL` when no recovery latch. The existing `requested and self._recovery_required: return` guard in `_ingest_cancel`'s stop branch must not swallow flatten cancels — the new branch returns first, so the emergency "canceled unexpectedly" path still fires only for cancels nobody requested.)

In `_ingest_reject` (~817) ROLE_EXIT branch, treat a flatten liquidation like the emergency reason — no second liquidation:

```python
        if order.role == ROLE_EXIT:
            if order.reason != "emergency_flatten" and self._pending_flatten is None:
                self._emergency_flatten()
            self._latch_recovery()
            raise TradovateStateError(
                f"exit order {order.order_id} rejected; recovery required"
            )
```

- [ ] **Step 4: Run** — `python3 -m pytest tests/test_tradovate_flatten_protocol.py tests/test_tradovate_broker.py -q` → all pass.

- [ ] **Step 5: Commit** — `git commit -am "flatten liquidates only after confirmed cancels; stop-fill race closes once (P0-2)"`

---

### Task 3: Resolution confirms flat + no working orders; one-bar deadline halts (P0-04, P1-5)

**Files:**
- Modify: `src/full_python/tradovate/broker.py` (`_resolve_pending_flatten`, `process_bar_open` at ~348)
- Test: `tests/test_tradovate_flatten_protocol.py`

**Interfaces:**
- Produces: hardened `_resolve_pending_flatten()` raising on residual working orders; `process_bar_open` deadline check: a `_pending_flatten` requested on an earlier bar that is still unresolved latches recovery and raises.

- [ ] **Step 1: Write the failing tests**

```python
def test_flatten_resolution_with_residual_working_order_latches():
    broker, rest = _entered_broker()
    stop_id = broker._working_stop_id
    _register_extra_working_exit_order(broker)   # simulate a stray working order
    broker.flatten(_bar(), "daily_limit")
    broker.ingest_raw_event(_cancel_event(stop_id))
    liq_id = rest.last_liquidation_order_id

    with pytest.raises(TradovateStateError, match="working order"):
        broker.ingest_raw_event(_fill_event(liq_id, action="Sell", price=99.0))

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_unresolved_flatten_on_a_later_bar_halts():
    broker, rest = _entered_broker()
    broker.flatten(_bar(), "daily_limit")        # cancel requested, never confirmed

    later = _bar(ts="2026-07-07T14:33:00Z")
    with pytest.raises(TradovateStateError, match="unresolved flatten"):
        broker.process_bar_open(later, _session(later))

    assert broker.execution_state == BrokerExecutionState.RECOVERY_REQUIRED


def test_dll_stays_latched_after_confirmed_flatten_but_no_recovery():
    broker, rest = _entered_broker()
    stop_id = broker._working_stop_id
    # Drive the DLL breach through process_bar_open at a deeply adverse price
    # (same construction the existing DLL tests in test_tradovate_broker.py use),
    # then confirm cancel + liquidation fill.
    _breach_daily_limit(broker)
    broker.ingest_raw_event(_cancel_event(stop_id))
    broker.ingest_raw_event(_fill_event(rest.last_liquidation_order_id, action="Sell", price=90.0))

    assert broker._daily_limit_hit is True       # entries blocked for the session
    assert broker._recovery_required is False    # P1-5: no dead latch
    assert broker.execution_state == BrokerExecutionState.NORMAL
```

`_bar(ts=...)`: extend the copied `_bar` helper with a timestamp parameter. `_breach_daily_limit` reuses the exact bar/session construction of the existing "daily loss" tests in `tests/test_tradovate_broker.py`.

- [ ] **Step 2: Run and verify failure.**

- [ ] **Step 3: Implement**

```python
def _resolve_pending_flatten(self) -> None:
    self._liquidation_in_flight = False
    pending = self._pending_flatten
    self._pending_flatten = None
    if pending is None:
        return
    if self._position is not None:
        self._latch_recovery()
        raise TradovateStateError(
            "flatten resolution with a position still open; recovery required"
        )
    if self._has_working_orders():
        self._latch_recovery()
        raise TradovateStateError(
            "flatten resolution with a residual working order; recovery required"
        )
    if not self._recovery_required:
        self._execution_state = BrokerExecutionState.NORMAL
```

In `process_bar_open`, immediately after `self._handle_session_rollover(session)`:

```python
        pending = self._pending_flatten
        if pending is not None and pending.requested_on_bar != bar.timestamp_utc:
            self._latch_recovery()
            raise TradovateStateError(
                f"unresolved flatten ({pending.reason}) from bar "
                f"{pending.requested_on_bar}; halting for review"
            )
```

One full bar is the deadline: every cancel/fill confirmation for a marketable order arrives within the same one-minute bar on this feed; anything slower is exactly the "remain halted and alert externally" case of the design (the raise reaches LiveLoop's handler, which writes the durable `execution_halt` ledger entry — that is the external alert).

- [ ] **Step 4: Run task + broker + full offline suite.** `python3 -m pytest -q` → zero failures.

- [ ] **Step 5: Commit** — `git commit -am "flatten resolution confirms flat + no working orders with one-bar deadline (P0-04, P1-5)"`

---

### Task 4: Calendar-driven session-close backstop (P0-03)

**Files:**
- Modify: `src/full_python/tradovate/broker.py` (`process_bar_open` at ~348, `_handle_session_rollover` message at ~370)
- Test: `tests/test_tradovate_flatten_protocol.py`

**Interfaces:**
- Consumes: `SessionInfo.minutes_from_midnight_et`, `SessionInfo.rth_close_minutes_et` (exchange-calendar authority; early closes populate 13:15 per `exchange_calendar.EARLY_CLOSE_MINUTES_ET`).
- Produces: broker-side flatten trigger `reason="session_close_backstop"` at close−1 minute whenever a position or working order still exists — strategy-independent belt-and-suspenders under the strategy's own backstop exit.

- [ ] **Step 1: Write the failing tests**

```python
def test_early_close_day_triggers_backstop_flatten_at_close_minus_one():
    broker, rest = _entered_broker()
    bar = _bar(ts="2026-07-03T17:14:00Z")  # 13:14 ET on the July 3 early close
    session = _session_with_close(bar, minutes=13 * 60 + 14, close=13 * 60 + 15)

    broker.process_bar_open(bar, session)

    assert broker._pending_flatten is not None
    assert broker._pending_flatten.reason == "session_close_backstop"


def test_normal_day_triggers_backstop_flatten_at_1559():
    broker, rest = _entered_broker()
    bar = _bar(ts="2026-07-07T19:59:00Z")
    session = _session_with_close(bar, minutes=15 * 60 + 59, close=16 * 60)

    broker.process_bar_open(bar, session)

    assert broker._pending_flatten is not None


def test_backstop_does_not_fire_before_close_minus_one_or_when_flat():
    broker, rest = _entered_broker()
    bar = _bar(ts="2026-07-07T19:58:00Z")
    session = _session_with_close(bar, minutes=15 * 60 + 58, close=16 * 60)
    broker.process_bar_open(bar, session)
    assert broker._pending_flatten is None

    flat_broker, flat_rest = _hydrated_order_broker()
    late = _bar(ts="2026-07-07T19:59:00Z")
    flat_broker.process_bar_open(late, _session_with_close(late, minutes=15 * 60 + 59, close=16 * 60))
    assert not flat_rest.liquidation_bodies and flat_broker._pending_flatten is None
```

`_session_with_close(bar, minutes, close)` builds `SessionInfo` directly (`dataclasses.replace` of `_session(bar)` with `minutes_from_midnight_et=minutes`, `rth_close_minutes_et=close`).

- [ ] **Step 2: Run and verify failure.**

- [ ] **Step 3: Implement** — in `process_bar_open`, after the unresolved-flatten deadline check and BEFORE the DLL check (a backstop flatten at the boundary must not be pre-empted by a same-bar DLL evaluation ordering surprise; first trigger wins, the other becomes a no-op through `_pending_flatten` idempotency):

```python
        close_minutes = session.rth_close_minutes_et
        if (
            close_minutes is not None
            and session.minutes_from_midnight_et >= close_minutes - 1
            and self._pending_flatten is None
            and not self._liquidation_in_flight
            and (self._position is not None or self._has_working_orders())
        ):
            if not self._config.flatten_enabled:
                raise TradovateStateError(
                    "session close reached with an open position and flatten_enabled=False"
                )
            self.flatten(bar, "session_close_backstop")
```

Update the `_handle_session_rollover` message (~370-372) from "the 15:59 backstop should have flattened" to "the session-close backstop should have flattened" — the fixed time is no longer the authority.

- [ ] **Step 4: Run** task tests + full suite (`python3 -m pytest -q`) → zero failures.

- [ ] **Step 5: Commit** — `git commit -am "broker-side session-close backstop from the exchange calendar, incl. early closes (P0-03)"`

---

### Task 5: Decision record, HANDOFF, whole-branch verification

**Files:**
- Create: `docs/decisions/2026-07-19-confirmed-flatten-session-boundaries.md`
- Modify: `HANDOFF.md` (§5 new bullet; §6 task 1 scope line)

- [ ] **Step 1:** Write the decision record with: findings closed in code (P0-2, P0-04, P1-5, P0-03), the staged state machine (request → FLATTEN_PENDING_CANCEL → FLATTEN_PENDING_FILL → confirmed resolution or halt), the routine-vs-emergency latch distinction, the one-bar deadline rationale, what remains open (P1-6 production event pump, P1-7 RiskManager veto, P1-8 restart/inherited-position recovery, Slice F partial quantities + full failure matrix, and every attended-DEMO drill), and the exact test list added.
- [ ] **Step 2:** HANDOFF §5: add a "Confirmed flatten and session boundaries (Slice E) — IMPLEMENTED OFFLINE (2026-07-19)" bullet in the same voice as the Slice A-D2 bullets; §6 task 1: remove "broker-confirmed 15:59/shutdown flatten" from the next-work list, leaving partial quantities, unknown-outcome recovery, the failure matrix, and the DEMO envelope items.
- [ ] **Step 3:** Full verification: `python3 -m pytest -q` (zero failures) and, with the anchor CSV available, `FULL_PYTHON_BASELINE_DATA=runs/baseline-anchor/nq1_2025-10-01_2026-06-26.csv python3 -m pytest -q` (identity tests must stay green — this slice must not touch sim/paper behavior).
- [ ] **Step 4:** Commit docs; open the PR titled "Slice E: confirmed flatten and session boundaries (P0-2, P0-04, P1-5, P0-03)" with the validation evidence in the body.

## Self-Review Notes

- Spec §Slice E coverage: calendar-driven close−1 trigger incl. early closes (Task 4); one confirmed-flatten protocol reused by DLL (Task 1-3 via `flatten()`), outage (LiveLoop already routes through `flatten()`), emergency recovery kept separate by design (its latch semantics are load-bearing); "flat + no working orders by a deadline, otherwise remain halted and alert externally" (Task 3; the halt's ledger entry is the external alert — a push channel is SP4 tooling). Shutdown flatten is deliberately NOT wired here: `close_end_of_data` stays operator-owned until the composition root (P1-6) exists to call it — recorded in the decision doc as an explicit open item.
- P1-5 is closed by subtraction: routine flattens no longer latch `RECOVERY_REQUIRED`; the states that do latch are all consumed (hydration reopens; halt is terminal-by-design).
- Type consistency: `PendingFlatten.awaiting_cancel_ids: frozenset` is consumed in Tasks 2-4 with set operations only; `requested_on_bar` compared as an exact timestamp string in Task 3's deadline check.
