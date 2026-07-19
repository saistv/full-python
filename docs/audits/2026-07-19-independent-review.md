# Independent correctness review — `saistv/full-python` at `3eab505` (2026-07-19)

## Scope and verdict

This is an offline-only review of `main` at
`3eab505d287d8e56d2ae51514104faa71cf2d443`, after PRs #25-#34. I read the
2026-07-13 adversarial audit and its status table first, then `HANDOFF.md`, then
all decision records newest-first. I treated every closure statement as an
unverified claim.

This is **not a clean bill**. The default-disabled Gate 5 literals still prevent
the repository's CLI from trading today, but the claims that P0-2, P1-6, P1-7,
and P1-8 are closed in offline code do not survive adversarial tracing. The most
immediate defect is that the composed order pump calls the real websocket client
with a zero timeout; the client returns without reading the transport. A broker
fill can therefore remain unseen and the protective stop never be submitted.

No product code or test was changed. The only repository change is this review.

## Verification record

- Clean branch point: local `HEAD` and `origin/main` both
  `3eab505d287d8e56d2ae51514104faa71cf2d443`.
- Required baseline, with operator-data variables removed:

  ```text
  $ env -u FULL_PYTHON_BASELINE_DATA -u FULL_PYTHON_TV_EXPORT python3 -m pytest -q
  779 passed, 5 skipped in 41.54s
  ```

- Thirteen independent adversarial probes exercised the real broker, risk
  manager, order pump, real `TradovateWebSocketClient`, and ODR report builder.
  They all reproduced the incorrect states described below:

  ```text
  13 passed in 0.46s
  ```

  These were throwaway review probes outside the checkout. A passing probe means
  the asserted bad outcome was reproduced; none was added to the repository.
- CI-gap mutation proof: in a throwaway checkout, I made
  `AdaptiveTrendStrategy.on_bar()` return an empty result only on the 14:39 UTC
  bar immediately preceding the committed 2025-11-27 14:40 UTC anchor entry.
  That entry is pinned at `tests/fixtures/golden_trades.json:405`, and the frozen
  overrides leave `SimulationConfig`'s `next_bar_open` default unchanged
  (`src/full_python/simulation/config.py:27-39`), so suppressing its preceding
  signal removes a known TradingView-matched trade.
  The exact CI command still returned:

  ```text
  779 passed, 5 skipped in 49.69s
  ```

  The causal link to the missing anchor is a source/fixture trace; the gated
  operator-data replay itself could not run without the absent operator data.

## Finding summary

| ID | Severity | Result |
|---|---|---|
| P0-1 | P0 | The production pump's default call never reads fresh frames from the real websocket transport; a filled entry can remain locally flat and unprotected. |
| P0-2 | P0 | Flatten/exit/cancel interleavings can recreate two live closing paths or strand an open position after its stop is gone. |
| P0-3 | P0 | Flatten progress and its deadline are bar-driven; final-bar shutdown, data outage, and liquidation rejection can leave unresolved or naked exposure with no further driver. |
| P0-4 | P0 | Startup flatten misclassifies inherited entry fills and corrupts multi-contract partial-fill state. |
| P1-1 | P1 | Pump heartbeats and event delivery have no adequate wall-clock bound; normal operation can exceed the documented 2.5-second heartbeat cadence, and disarmed operation is unbounded. |
| P1-2 | P1 | The shared risk veto uses different fallback reference bars in sim and live, so “live can no longer submit an order sim refuses” is false. |
| P1-3 | P1 | Successful race/flatten paths leave stale journal or pending state; one needs hydration and the other blocks that promised hydration. |
| P2-1 | P2 | There is still no source-wired runnable order-enabled entry point, and `ServerSim`'s timing model invalidates the broad composition-level conclusions. |
| P2-2 | P2 | CI can merge a parity-breaking strategy change green; five anchor/parity assertions exist only on an operator machine. |
| P2-3 | P2 | The research evaluator's reconciliation-integrity gate can false-pass a trade that violates the frozen entry mechanism. |

## P0-1 — The composed pump does not read the real user-sync socket

**Claim contradicted:** `HANDOFF.md:320-334` and
`docs/decisions/2026-07-19-order-pump-and-veto.md:23-39` say P1-6 is closed by a
production caller that drains user-sync events into the hardened broker.

**Trace:**

1. Start hydrated and flat. Submit entry order `101`; the REST call returns its
   ID, so the broker registers it at `src/full_python/tradovate/broker.py:561-583`.
   A valid fill props frame is now waiting in the websocket transport.
2. The composed maintenance callback calls `pump.pump()` with its default
   `max_wait_seconds=0.0` at
   `src/full_python/live/order_runner.py:139-142`.
3. `OrderEventPump` forwards zero to the first `receive_event` at
   `src/full_python/tradovate/order_pump.py:65-90`.
4. Unless an event was already decoded into `_pending_events`, the real
   `TradovateWebSocketClient.receive_event(0)` computes an expired deadline and
   returns at `src/full_python/tradovate/ws.py:93-105` **before calling
   `transport.receive`**.
5. Entry-fill recognition and stop submission occur only after ingestion at
   `src/full_python/tradovate/broker.py:788-831`.

Executed result:

```text
after default pump:
  delivered=0
  transport.receive_calls=0
  broker.position=None
  REST order types=['Market']

after pump(max_wait_seconds=1.0):
  delivered=1
  transport.receive_calls=1
  broker.position=long 1
  REST order types=['Market', 'Stop']
```

The incorrect outcome is a real filled entry that the adapter still models as
flat, with no protective stop submitted. The unit websocket and `ServerSim`
mask the defect by popping queued objects even when their `wait_seconds` input is
zero (`tests/test_tradovate_order_pump.py:23-27` and
`tests/test_failure_matrix_e2e.py:105-108`).

The narrower REST-response ordering question does not produce a separate race in
the current single-thread topology: a physical socket event can arrive during
the synchronous REST call, but nothing reads it until control returns; on a
successful REST response, registration completes before the next maintenance
call. If the REST outcome is unknown, registration never occurs and a later
event reaches `_known_order`, which halts on the unknown ID at
`src/full_python/tradovate/broker.py:779-786`. That is fail-closed, but it remains
the separately documented open recovery-association problem.

## P0-2 — Flatten and exit interleavings violate the single-close invariant

**Claim contradicted:** `HANDOFF.md:307-318` and
`docs/decisions/2026-07-19-confirmed-flatten-session-boundaries.md:14-29,68-74`
state that two live closing orders can no longer coexist.

There are three independently reproduced counterexamples.

### A. A duplicate cancel event starts a second closing path

`order_events.py` explicitly says duplicate cancels are tolerated
(`src/full_python/tradovate/order_events.py:10-11`), but `_ingest_cancel` has no
terminal-event idempotency check (`src/full_python/tradovate/broker.py:1061-1100`).

```text
state: long 1; protective stop 102 working
action: strategy exit -> request cancel 102
event: first Canceled(102) -> market exit 103 working
event: duplicate Canceled(102)
outcome:
  cancel requests=[102, 103]
  local exit 103 status=working
  emergency liquidation 104 submitted before cancel 103 is confirmed
  TradovateStateError raised
```

On the duplicate, the old stop is still role `protective_stop`, the local
position is still open, and the normal requested-cancel branch no longer
matches. `_ingest_cancel` calls `_emergency_flatten`; that path requests cancel
of exit `103` but does not await confirmation before submitting liquidation
`104` (`src/full_python/tradovate/broker.py:883-927,1090-1100`). Exit `103` and
liquidation `104` can therefore coexist during the cancel-confirmation window;
if `103` fills before its cancel and `104` also fills, they reverse the account.

### B. A same-bar backstop/DLL flatten and strategy exit issue two cancels

`LiveLoop` calls `process_bar_open` and then still calls the strategy and
`apply_strategy_result` on the same bar
(`src/full_python/execution/live_loop.py:72-102`). The close/DLL path can start a
flatten at `src/full_python/tradovate/broker.py:403-429`, but the strategy-exit
path does not check `_pending_flatten` and unconditionally calls the cancel path
at `src/full_python/tradovate/broker.py:488-510,930-942`.

```text
after close-1 flatten:
  execution_state=FLATTEN_PENDING_CANCEL
  cancel posts=[102]

after same-bar strategy exit:
  execution_state=EXIT_PENDING_CANCEL
  cancel posts=[102, 102]
  cancel journal states=[REQUEST_ACCEPTED, REQUEST_ACCEPTED]
```

The second request overwrites the per-order cancel-intent mapping. One cancel
event confirms only the second journal record; the first remains
`REQUEST_ACCEPTED`. If the duplicate POST rejects, the loop enters
`RECOVERY_REQUIRED` with the long open and no liquidation, while the first
accepted cancel may still remove the protective stop.

### C. Exit rejection is suppressed while a flatten awaits that exit's cancel

```text
state: long 1; stop 102 already canceled; strategy market exit 103 working
action: flatten requests cancellation of 103
event: Rejected(103) arrives before Canceled(103)
outcome:
  position=long 1
  stop 102=canceled
  exit 103=rejected
  pending_flatten still awaits 103
  liquidation posts=0
  execution_state=RECOVERY_REQUIRED
```

For an exit rejection, emergency liquidation runs only when
`_pending_flatten is None` (`src/full_python/tradovate/broker.py:1053-1058`). In
this interleaving the pending flatten suppresses the emergency, even though the
order it was waiting to cancel has just rejected and the position has no stop.

## P0-3 — Flatten has no independent progress/deadline driver

**Claim contradicted:** the staged protocol claims a one-bar deadline and a
session-close backstop sufficient to confirm flat or halt
(`HANDOFF.md:307-318`; decision record lines 24-34).

The “deadline” exists only at the next call to `process_bar_open`
(`src/full_python/tradovate/broker.py:383-399`). `close_end_of_data` is a no-op
(`src/full_python/tradovate/broker.py:588-591`). A final close-minus-one bar
therefore reproduced:

```text
process final 15:59 bar -> FLATTEN_PENDING_CANCEL
position=long 1
cancel posts=1
liquidation posts=0
close_end_of_data() -> unchanged; flatten_in_progress=True
```

`bars_until` performs maintenance only after the yielded bar is processed and
then may return immediately at the wall-clock end
(`src/full_python/live/runner.py:84-100`). If confirmation is not available in
that single maintenance call, no later bar invokes the deadline. A cancel can
then complete at the broker, removing the stop, while the local process exits
with the position still open.

A data outage during `FLATTEN_PENDING_FILL` has the same missing-driver result.
The outage handler calls `flatten` once and immediately returns
(`src/full_python/execution/live_loop.py:120-138`); `flatten` is a no-op while a
flatten or liquidation is pending
(`src/full_python/tradovate/broker.py:602-603`). No further pump, fill handling,
deadline, or reconciliation occurs.

Liquidation failure after confirmed cancels is also terminal in the wrong state.
`_liquidation_in_flight` is set **before** submission and is not cleared on a
rejected response (`src/full_python/tradovate/broker.py:682-707,1142-1173`).
Executed result:

```text
state before submit: long 1; stop 102 confirmed canceled
liquidation REST response: failureReason=market_closed
outcome:
  position=long 1
  stop 102=canceled
  pending_flatten=True
  liquidation_in_flight=True
  execution_state=RECOVERY_REQUIRED
  later flatten("retry") -> no-op; total liquidation attempts remains 1
```

The happy-path tests correctly prove that a same-bar, ordered, full cancel plus
full liquidation ends `NORMAL`. They do not make these failure paths safe.

## P0-4 — Startup flatten cannot safely represent inherited fills

**Claim contradicted:** `HANDOFF.md:335-345` and
`docs/decisions/2026-07-19-startup-flatten.md:18-48` describe inherited position
and working-order sets as routed through a confirmed close, with races and
failures halting latched and fresh hydration reopening the account.

### A. A working inherited entry can fill into unmodeled exposure

Every inherited working order is registered as `ROLE_INHERITED`, regardless of
whether it opens or closes exposure
(`src/full_python/tradovate/broker.py:634-645`). Every fill for a non-entry role
routes to `_on_exit_fill`; that function raises immediately if local position is
flat (`src/full_python/tradovate/broker.py:788-800,989-994`).

```text
startup snapshot: position=None; inherited Buy order 555 working
startup action: cancel 555 requested
event: Fill(555, Buy, qty=1) wins the cancel race
outcome:
  adapter position=None
  liquidation posts=0
  flatten still awaits 555
  TradovateStateError: exit fill ... while flat
```

The real account has opened a long position while the adapter remains flat and
does not invoke the entry-fill emergency or submit protection.

### B. A multi-contract partial liquidation fill poisons terminal state

The startup path accepts an inherited position of any quantity. User-sync fill
translation maps every `Created` fill entity to ordinary `kind="fill"`; it never
emits `partial_fill` (`src/full_python/tradovate/order_events.py:84-90,127-147`).
The broker marks the order `filled` before validating the close quantity
(`src/full_python/tradovate/broker.py:788-800`), while `_on_exit_fill` requires
one fill exactly equal to the full position
(`src/full_python/tradovate/broker.py:989-1004`).

```text
startup snapshot: inherited long 3, no working orders
action: liquidation order 101 submitted for qty=3
event: first real fill Sell qty=1
outcome:
  TradovateStateError: partial closes not modeled
  adapter position remains long 3 (real residual is long 2)
  order 101 status=filled
event: later Sell qty=2 -> TradovateStateError: duplicate fill
```

The 2026-07-19 Slice F decision accurately discloses that multi-contract partial
lifecycle is deferred, but startup flatten is not restricted to one inherited
contract. The unqualified P1-8 recovery closure therefore exceeds the supported
state space.

The placeholder review found no additional standalone defect: a blank inherited
side is rejected by production hydration at
`src/full_python/tradovate/account_sync.py:277-283`; an omitted `orderQty` can
register as zero at `broker.py:641-642`, but the cancel-only path uses the order
ID and terminal status. The dangerous inherited-entry fill above is independent
of that placeholder quantity.

## P1-1 — Heartbeats and critical events are not time-bounded

**Claim contradicted:** the pump decision says it sends application heartbeats
“on the runtime cadence” and reconciles on a bounded interval
(`docs/decisions/2026-07-19-order-pump-and-veto.md:29-34`).

`OrderEventPump` checks the 2.5-second heartbeat only when `pump()` is entered
(`src/full_python/tradovate/order_pump.py:29,65-82`). Production maintenance is
called only after a bar has been yielded and processed
(`src/full_python/live/runner.py:84-100`). `LiveBarSource` can wait through the
next expected minute plus 25 seconds of grace while armed, and it loops without a
finite bound while disarmed
(`src/full_python/livedata/live_bar_source.py:64-89,112-143`). Reconciliation
also performs synchronous HTTP work whose individual socket operations use a
30-second timeout (`src/full_python/tradovate/http.py:59-85`); that is another
source of delay, not a proven additive end-to-end bound.

The repository's vendor contract says `[]` is required every 2.5 seconds
(`docs/superpowers/specs/2026-07-15-account-sync-runtime-design.md:35-36`). The
separate account-runtime implementation uses 7.5 seconds without inbound
activity as its own liveness threshold
(`src/full_python/tradovate/account_runtime.py:128-166,322-343`); the repository
does not identify that value as a vendor threshold. Nor do the checked-in
offline sources pin a numeric vendor disconnect/socket-timeout threshold: an
older design's `websocket_timeout_seconds=10.0` is absent from the implemented
config (`docs/superpowers/specs/2026-07-07-tradovate-adapter-design.md:139-150`;
`src/full_python/tradovate/config.py:30-49`). Against the timing contract the
repository does pin, the order-pump path has:

- approximately 60-second heartbeat gaps with normal one-minute bars;
- up to approximately 85 seconds before an armed missing-bar outage surfaces,
  depending on when the outage begins relative to the expected bar;
- additional blocking during synchronous REST socket operations; and
- no finite bound outside the armed window.

The worst case is therefore **unbounded**, not the 2.5-second cadence, and it
necessarily exceeds any finite vendor socket-timeout threshold. Unlike the D2
runtime, `OrderEventPump` never reads `last_transport_activity` and has no
liveness invalidation. A precise finite vendor-threshold comparison is itself
unsupported by the repository's offline evidence.

The `512` event cap is not a critical-event bound either
(`src/full_python/tradovate/order_pump.py:30,84-101`). Every props frame counts,
including non-lifecycle frames that translate to nothing. Executed burst:

```text
flatten awaits cancel 102
queue: 512 cashBalance frames, then Canceled(102) as frame 513
first pump: delivered=0; cancel remains queued; no liquidation submitted
next bar opens before next maintenance: unresolved-flatten deadline raises
```

With the real websocket wrapper, the zero-timeout defect in P0-1 is stricter:
after the first transport frame, a zero-wait receive can inspect only events
decoded from that same frame; it does not read a second fresh frame.

## P1-2 — “Sim-identical” risk reasons diverge without `signal_price`

**Claim contradicted:** `HANDOFF.md:320-325` and the pump/veto decision lines
10-19 say live can no longer submit an order the simulator refuses.

The simulator processes the intent before updating its previous-bar pointer
(`src/full_python/simulation/engine.py:59-69`) and falls back to the **previous**
bar close at `src/full_python/simulation/position_engine.py:481-511`. The broker
falls back to the **current** `bar.close` at
`src/full_python/tradovate/broker.py:528-542`.

Executed divergence:

```text
previous close=100
current close=110
intent=Buy 1, stop_price=105, signal_price absent

sim reference=100 -> invalid_stop -> no order
live reference=110 -> accepted -> Market order 101 POSTed and Acked
```

The inverse price move gives the inverse discrepancy. Current production
strategies normally populate `signal_price`, so this is not a claim that the
present Adaptive Trend output hits the edge. It is nevertheless part of the
public `OrderIntent` contract, the fallback code exists on both sides, and the
absolute “exact/sim-identical” closure claim is false.

## P1-3 — Nominal resolution leaves stale pending state

Two reproduced paths report a resolved or clean account but retain state that
prevents the promised recovery.

First, if a strategy exit is already `EXIT_PENDING_CANCEL` and a flatten is
requested for the same stop, `_begin_flatten` reuses the requested cancellation.
The cancel event then takes the pending-flatten branch and returns before the
pending-exit branch (`src/full_python/tradovate/broker.py:1061-1094`). The later
liquidation fill resolves the flatten but never clears `_pending_exit`:

```text
after liquidation fill:
  position=None
  execution_state=NORMAL
  pending_exit still set
next entry -> Rejected(reason="position_already_open")
```

`_entry_is_stable_flat` requires `_pending_exit is None` at
`src/full_python/tradovate/broker.py:1277-1286`, so the apparent normal state is
not reusable without another hydration.

Second, when the inherited protective order fills before its requested cancel,
startup flatten correctly suppresses liquidation and reaches flat, but the cancel
journal remains `REQUEST_ACCEPTED`. Fresh hydration accepts that cancel only if
the target order status is `Canceled` or `Expired`, not the actual terminal
`Filled` status (`src/full_python/tradovate/broker.py:272-303`).

```text
after inherited stop wins: position=None; RECOVERY_REQUIRED
cancel intent=REQUEST_ACCEPTED; broker order 555=Filled
fresh stable-flat hydrate -> accepted cancel intent ... not confirmed terminal
```

That contradicts the startup claim that the stop-wins-cancel race resolves and a
fresh stable-flat hydration can then reopen entries.

## P2-1 — No runnable order-enabled entry point; the matrix fake is forgiving

### Reachability

A source-wide call search finds `build_order_session`, `run_startup_flatten`, and
`startup_flatten` called only by tests; their source occurrences are definitions
and docstrings. `order_runner.main()` builds a config with both mutation flags
literal-false and then unconditionally raises `SystemExit` without constructing
the REST client, user-sync websocket, hydrator, bar source, pump, or loop
(`src/full_python/live/order_runner.py:72-98,186-223`). The package's executable
entry point imports the observe-only runner
(`src/full_python/live/__main__.py:1-5`).

The new objects, including the executable `build_order_session()` callable, are
useful composition-library code, but
`docs/decisions/2026-07-19-order-pump-and-veto.md:23-39` does not establish the
claimed source-wired runnable production caller, and the startup sequence asserted at
`docs/decisions/2026-07-19-startup-flatten.md:46-48` is not wired in source.

### Every forgiving `ServerSim` behavior found

| Dimension | `ServerSim` behavior | Missing real-broker behavior / dependent conclusions |
|---|---|---|
| Zero-timeout read | Pops its queue even at `wait_seconds=0` (`tests/test_failure_matrix_e2e.py:105-108`). | Masks P0-1; every lifecycle conclusion depends on events actually being read. |
| Market fills | Every market order queues a full one-lot fill before the REST call returns (`:135-145`). | No delayed/no fill, partial, rejection, malformed response, or response/event race; entry/exit and exactly-once conclusions depend on this. |
| Stop behavior | Stop orders never trigger server-side. | The stop-vs-cancel and stop-vs-liquidation races are absent. |
| Cancels | Every cancel succeeds and immediately queues terminal `Canceled` (`:147-151`). | DLL, exit, early-close, and startup success all depend on same-call confirmation. |
| Liquidation | Always queues an immediate full fill; when fake position is zero it invents quantity one (`:153-163`). | No residual, partial, reject, delay, or already-flat semantic; flatten/startup conclusions depend on this. |
| Ordering | One FIFO list; events are unique, in order, never duplicated, reordered, dropped, delayed, or adversarially batched (`:91,105-131`). | Masks duplicate-cancel, event-513, and cross-order interleavings. |
| Concurrency | REST returns before maintenance can ingest the event it just queued. | Cannot test an event physically arriving before the placement response. |
| Position truth | `_net_pos` changes when the fill is queued, not delivered; `position_list()` returns a separate manually scripted list (`:99,111-113,165-167`). | REST-drift row proves only a static injected mismatch, not split-sync timing or exposure before detection. |
| Price/time | One fixed mark and a hard-coded fill timestamp (`:97,119`). | No slippage, bar-boundary, late-fill, or clock-order behavior. |
| Identity/schema | Event identity is always benign; no foreign/malformed identity reaches the composed tests. | Identity conclusions come from component tests, not the matrix. |
| Transport | Never blocks, disconnects, fragments, times out, backpressures, or enforces heartbeat/liveness. | The pump's socket and heartbeat claims receive no composition evidence. |
| Burst | No test approaches `_MAX_EVENTS_PER_PUMP`. | Critical-event delay under a silent-frame burst is untested. |
| Startup truth | Terminal snapshot is manufactured from the local journal and cancel request bodies (`:60-84`). | Startup “fresh broker truth” is not independent. |
| Bar ordering | Test maintenance runs **before** every bar (`:170-184`); production runs it after a yielded bar (`src/full_python/live/runner.py:93-96`). | Initial unknown-event timing and final-bar confirmation/deadline behavior differ. |

The seven advertised conclusions therefore have the following actual scope:

| Matrix row/conclusion | Reality |
|---|---|
| Entry → stop → strategy exit; exactly-once feedback | Proven only with immediate ordered full fills, immediate cancel, no duplicate/reject, and the forgiving zero-timeout fake. |
| DLL staged flatten ends `NORMAL` | Proven only when cancel and full liquidation resolve FIFO inside the same pump call. |
| Holiday `market_closed` veto with zero POSTs | Holds independently of lifecycle timing. |
| Early-close backstop | Trigger time is proven; successful completion assumes immediate ordered confirmations and another maintenance opportunity. |
| Unknown-order fill halt | Proves a directly prequeued unknown ID; test maintenance delivers it before the first bar, unlike production. It is not the placement-response race. |
| REST position drift halt | Proves detection of a static scripted mismatch only. |
| Startup flatten → clean session | Depends on one-lot full fill, immediate FIFO cancel, and locally fabricated terminal state. |

## P2-2 — CI does not guard parity or full-anchor identity

`.github/workflows/ci.yml:3-7,29-32` explicitly runs only the offline suite and
provides neither operator-data variable. The two always-on TradingView tests read
only the committed `golden_trades.json` and assert its static session set/count
(`tests/test_tv_reconciliation.py:32-48`); they never run the current engine.

The throwaway mutation proof described in the verification record deleted a
known anchor signal while the CI-equivalent run remained exactly `779 passed, 5
skipped`. A parity-breaking strategy change can therefore merge green.

Everything currently verified only on the operator's machine is:

| Skipped test | Operator-only assertion |
|---|---|
| `tests/test_golden_trades.py:49-77` | Current engine exactly reproduces all frozen-anchor trade timestamps, reasons, entry/exit prices, and P&L. |
| `tests/test_live_loop_identity.py:161-187` | Production strategy produces identical full-anchor trades through `SimulationEngine` and `PaperBroker`/`LiveLoop`. |
| `tests/test_live_session_report.py:311-355` | Real anchor slice contains nonempty signals and the report detects a corrupted intent. |
| `tests/test_sizing_candidates.py:23-58` | Frozen-window NQ/MNQ sizing, instrument identity, and nonempty trade populations. |
| `tests/test_tv_reconciliation.py:51-81` | Current engine matches the operator TradingView export 106/106, with no quantity mismatches and `$0.00` maximum entry-price delta. |

CI exists, so the prior audit's literal “no CI” P2-3 is closed. The stronger
parity/anchor evidence gap remains.

## P2-3 — The ODR reconciliation-integrity gate can false-pass

The v3 verdict documented seven reconciliation false positives and directed that
the two evaluator rules be corrected only prospectively
(`docs/research/2026-07-18-overnight-displacement-reversal-v3-verdict.md:147-157`).
I did not reopen or rescore the sealed rejection. I tested the reverse failure:
whether corrupted mechanism evidence can receive a zero-violation PASS.

Starting from the otherwise-clean research test ledger/report, I changed only
the accepted signal bar's close from `108` to `110`. The snapshot is an up-gap
short with `rth_open=110` and `dtr20=100`; the frozen strategy requires the
decisive close to be at or below `109`
(`src/full_python/strategy/overnight_displacement_reversal.py:948-955`). The
accepted signal, intent, fills, and profitable trade were left unchanged.

Executed result:

```text
signal bar close=110
required decisive close <=109
reconciliation_violation_count=0
reconciliation_violations=[]
zero_reconciliation_violations=True
```

The evaluator builds `bar_by_timestamp`
(`src/full_python/research/overnight_displacement_reversal.py:559-564`) but the
accepted-signal checks compare signal metadata only with intent metadata
(`:693-759`). They never recompute decisive-close, extension, close-location,
structural-extreme, risk, or target geometry from the signal bar. The primary
gate trusts only `reconciliation_violation_count == 0`, and the final verdict is
`all(checks.values())` (`:1802-1806`). The executed small fixture did not pass
the unrelated economic/sample gates; it proves a false PASS of the
reconciliation-integrity gate. A full run whose other gates pass could therefore
receive an overall false PASS even though an accepted trade violates the frozen
mechanism.

The old global reverse-exit lookup that produced false positives is also still
present at `:1020-1029`; the prospective evaluator correction requested by the
v3 verdict has not yet been applied.

## Claims versus reality — ten sampled 2026-07-19 claims

| # | Claim sampled from `HANDOFF.md` §5 / 2026-07-19 decisions | Reality at `3eab505` |
|---:|---|---|
| 1 | Staged flatten means two live closing orders cannot coexist (`HANDOFF.md:307-318`). | **Contradicted.** Duplicate cancel delivery submits liquidation `104` while exit `103`'s cancel is unconfirmed, creating a coexistence window; other exit/flatten interleavings strand naked exposure (P0-2). |
| 2 | An unresolved flatten has a one-bar deadline (`confirmed-flatten...md:30-34`). | **Partly true.** It raises only if a later bar calls `process_bar_open`; final-bar shutdown and data-outage exit provide no deadline (P0-3). |
| 3 | A routine confirmed flatten ends `NORMAL` while the DLL latch blocks entries (`HANDOFF.md:313-315`). | **Verified for the ordered full-fill happy path.** Existing test and direct trace agree. It does not cover P0-2/P0-3 interleavings. |
| 4 | The broker applies the exact sim veto and cannot submit what sim refuses (`HANDOFF.md:320-325`). | **Contradicted.** Missing `signal_price` uses previous close in sim and current close in live (P1-2). |
| 5 | P1-6 is closed by a production caller that drains events, heartbeats, and reconciles (`HANDOFF.md:325-333`). | **Contradicted.** The order-enabled path is not source-wired into a runnable entry point; its composed default pump performs zero real socket reads and has no heartbeat/liveness bound (P0-1/P1-1/P2-1). |
| 6 | Startup inherited state is confirmed-flat and entries reopen after fresh hydration (`HANDOFF.md:335-345`). | **Contradicted over the supported input domain.** Inherited entry fills and multi-contract partials corrupt local state; a stop-win race cannot pass fresh hydration (P0-4/P1-3). |
| 7 | The startup recovery sequence is wired in the composition root (`startup-flatten.md:46-48`). | **Contradicted.** No source-wired runnable caller exists; invocations are test-only and `order_runner.main()` exits before composition. |
| 8 | Seven rows are proven through the “REAL composed stack” (`slice-f-offline-closure.md:9-25`). | **Overstated.** Real classes are composed, but the fake timing/state model removes the races on which most conclusions depend (P2-1). |
| 9 | A confirmed cancel crosses rollover cleanly; an unconfirmed one halts (`slice-f-offline-closure.md:43-46`). | **Verified at broker-unit scope.** `tests/test_tradovate_broker.py:1834-1868` pins both directions. |
| 10 | P3-4 account guessing is closed and Gate 5 flags remain literal-false (`slice-f-offline-closure.md:37-41`; pump/veto decision lines 41-48). | **Verified.** Explicit-or-unambiguous observe selection is enforced at `live/runner.py:220-250`; order-runner selection verifies ID+name, and both flags are literals at `live/order_runner.py:72-98`. |

## Status against the 2026-07-13 audit table

| Prior finding | Claimed status after PRs #25-#34 | Independent result |
|---|---|---|
| P0-2 staged flatten race | Closed offline | **REOPEN.** The exact original stop-fill-before-cancel case is fixed, but duplicate terminal events and pending exit/flatten interleavings recreate dual-close and naked-position states. |
| P1-5 dead routine-recovery latch | Closed | **Narrow closure verified.** Happy-path routine flatten ends `NORMAL`; stale pending/journal states remain under interleavings (P1-3). |
| P1-6 no production event pump | Closed in code | **OPEN.** Factory is not source-reachable, and its default pump cannot read the real transport. |
| P1-7 no live veto | Closed | **PARTIAL.** Shared module/reason strings exist, but the missing-price reference contract diverges. |
| P1-8 no startup recovery | Closed offline | **OPEN.** Stable-flat refusal prevents immediate doubling, but the advertised recovery path is unwired and unsafe for inherited-entry/partial-fill states. |
| P2-3 no CI | Closed | **Literal closure verified; evidence scope remains inadequate.** CI does not exercise current-engine parity or full-anchor identity. |
| P2-5 rollover false halt | Closed | **Verified at unit scope.** |
| P3-4 account auto-selection | Closed | **Verified.** |

The literal order/flatten flags remain false, so these are pre-enable blockers
rather than evidence of a live incident. They are nevertheless code-correctness
failures in precisely the offline closures asserted by PRs #25-#34.
