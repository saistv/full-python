# Adversarial Audit — saistv/full-python @ main (dce7988), 2026-07-13

Offline suite: **397 passed, 4 skipped**. All numeric claims below were
reproduced from raw data on this commit unless marked otherwise.

---

## STATUS OF THESE FINDINGS (updated 2026-07-14)

**FIXED** on `claude/restore-research-authority` (PR #25) — see
`docs/decisions/2026-07-13-exchange-calendar-correction.md`:

| Finding | Status |
|---|---|
| P0-1 — cash-equity calendar deletes 7 open trading days; TV parity 106/106 → 103/106 | **FIXED.** Calendar pinned to a 1,379-weekday exchange fixture; parity restored to 106/106, $0.00 delta; golden fixture rebuilt from the reconciliation; regression test added. |
| P1-3 — forming-bar snapshots counted as "ignored events"; feed dies at the 09:30 open | **FIXED.** Snapshots are progress. |
| P1-4 — one early realtime snapshot discards all warmup history; `eoh` unhandled | **FIXED.** Gather-and-sort staging until `eoh`, bounded 30s grace. |
| P1-4b — shadow report cannot detect a truncated capture | **FIXED.** `check_bar_coverage` asserts warmup + full entry window. |
| Feed errors escape the halt protocol | **FIXED.** `TradovateFeedError` is now a `LiveDataError` → flatten + halt. |

**STILL OPEN — these are the live-execution backlog.** Every one of them blocks
`order_enabled`, and none is a patch: the order path is not built, it is absent.

| Finding | Status |
|---|---|
| **P0-2** — `flatten()` does not wait for cancel confirmation (the discipline applied to `result.exits` was never applied to the flatten path); a stop filling during a DLL flatten can leave two closing orders live | **OPEN** |
| **P1-5** — `RECOVERY_REQUIRED` is set by the *routine* DLL flatten, never cleared, and read by nothing | **OPEN** |
| **P1-6** — no account-event pump: `ingest_raw_event` / `reconcile_rest_positions` have **no production caller**, so the entire hardened order lifecycle is unreachable | **OPEN** |
| **P1-7** — `TradovateBroker` applies no `RiskManager` veto; it will submit orders the simulator refuses | **OPEN** |
| **P1-8** — no restart/recovery: the adapter starts flat and will double a live position | **OPEN** |
| P2-1 — "walk-forward" folds are all inside the original TV selection window; the one true pre-selection window (2021-03→2022-12) is omitted from the fold table | **OPEN** |
| P2-2 — ablation has no significance test | **OPEN** |
| P2-3 — no CI | **OPEN** |
| P2-4 — phantom deps (numpy/pandas declared, zero imports) | **OPEN** |
| P2-5 — session rollover halts on cancels never confirmed (blocked on P1-6) | **OPEN** |
| P3-1 — documented 31pt stop cap understates realized risk (48% of trades exceed it; max 33.75pt = $675/contract) | **OPEN** |
| P3-2/3/4 — research knobs in production config; broker commission defaults to 0.0; `accounts[0]` auto-selection | **OPEN** |

The original findings, evidence and executable reproductions follow unchanged.

---

## P0-1 — The exchange calendar declares 7 open trading days per year "closed": an unregistered strategy filter that rebaselined research outside Gate 1 and voided the TV-parity authority (106/106 → 103/106)

**Severity:** P0. *Not* because the numbers are wrong — they reproduce exactly, and
the P&L delta is immaterial (+$965 / 5 yr). Because (a) a strategy filter was
shipped as a "correctness fix", silently redefining the baseline every future
candidate is judged against, and (b) it deleted trades that TradingView actually
took, destroying the trade-level reconciliation that is the engine's entire claim
to authority.
**Files:** `src/full_python/data/exchange_calendar.py:55-102`; `src/full_python/data/sessions.py:57-66`; `tests/fixtures/golden_trades.json` (rewritten 115→112)

**Invariant violated:** the simulator must model the market that exists.
`full_holidays()` uses the **US cash-equity** holiday set and returns
`rth_close_minutes_et = None` (⇒ `is_rth=False`, entries vetoed `market_closed`)
for MLK, Presidents Day, Memorial Day, Juneteenth, July 4, Labor Day and
Thanksgiving. CME equity-index futures **trade an abbreviated 09:30–13:00 ET
session on every one of those days.**

**Evidence (Databento GLBX, the exchange's own record, 5-yr file):**

| ET date | calendar says | actual RTH bars | bars in 09:30–10:00 | volume in window | last bar |
|---|---|---:|---:|---:|---|
| 2026-01-19 MLK | FULL HOLIDAY | 210 | 30 | 15,268 | 23:59 |
| 2026-02-16 Presidents | FULL HOLIDAY | 210 | 30 | 15,343 | 23:59 |
| 2026-05-25 Memorial | FULL HOLIDAY | 210 | 30 | 7,497 | 23:59 |
| 2025-11-27 Thanksgiving | FULL HOLIDAY | 210 | 30 | 3,961 | 21:44 |
| 2026-06-19 Juneteenth | FULL HOLIDAY | 210 | 30 | 7,766 | 12:59 |
| 2025-07-04 | FULL HOLIDAY | 210 | 30 | 6,064 | 12:59 |
| 2025-09-01 Labor Day | FULL HOLIDAY | 210 | 30 | 6,812 | 23:59 |
| 2025-12-25 Christmas | FULL HOLIDAY | **0** | 0 | 0 | — |
| 2026-01-01 New Year | FULL HOLIDAY | **0** | 0 | 0 | — |
| 2026-04-03 Good Friday | FULL HOLIDAY | **0** | 0 | 0 | 09:14 |

Only 3 of 10 are genuine closures. The other 7 are **210-bar abbreviated
sessions** (09:30→13:00 ET) with a fully open entry window.

**Parity regression (decisive):** the 3 trades the calendar removed from the
9-month anchor were checked against `runs/baseline-anchor/reconciliation.json`
(the 106/106 TV reconciliation). None appear in `extra_in_sim` ⇒ **all three
matched real TradingView trades**:

```
2025-11-27  short  entry 14:40Z @ 25293.75  session_end   -$185
2026-01-19  short  entry 14:46Z @ 25239.25  stop          -$345
2026-05-25  long   entry 13:38Z @ 29969.25  stop          -$400
```

115 old sim trades = 106 TV-matched + 9 pre-TV-history extras. Remove 3 matched
⇒ **the engine now reconciles 103/106**. No re-reconciliation was run;
`golden_trades.json` was regenerated to 112 trades, so the one committed artifact
linking the engine to TradingView now pins the new behavior and can no longer
detect the 103/106 regression. (The golden test is in any case self-referential:
it replays the sim and compares against a fixture the sim produced — a valid
regression guard, but not parity evidence.)

**It also fails the project's own Gate 1.** 5-yr delta from the filter is
**+$965** (829/$159,160 → 813/$160,125) — far below the locked $10,000
materiality bar. Shipped as a "Phase 0 correctness fix," it is in fact an
**unregistered strategy filter that would be REJECTED** by the promotion table
it now silently defines the baseline for. Every Phase 0/1/2 number and the MNQ
pilot decision are computed on this baseline.

**Secondary:** `EARLY_CLOSE_MINUTES_ET = 13*60` is also wrong. On the 3 real
early closes (July 3, Black Friday, Christmas Eve) the data's last bar is
**13:14** ⇒ futures close 13:15 ET, not 13:00.

**Consequence:** (1) the research baseline now contains an unregistered filter, so
no future candidate can be judged honestly against it; (2) trade-level TV parity —
the engine's authority — is broken and unmeasured; (3) **sim and live diverge on
those 7 days/year**: the simulator and PaperBroker veto entries (`market_closed`),
while `TradovateBroker` has no veto layer and *would submit an order* into the
open, thinner holiday market (see P1-7, Probe F). The "identity by shared code"
claim fails precisely on the days the calendar is wrong.

**Smallest fix:** per-day close map — full closure {Good Friday, Christmas, New
Year}; abbreviated 13:00 ET close {MLK, Presidents, Memorial, Juneteenth, Jul 4,
Labor, Thanksgiving}; early 13:15 ET close {Jul 3, Black Friday, Christmas Eve}.
Keep entries enabled on all open days; move only the backstop. Then re-run
`reconcile` against the TV export and restore 106/106 before regenerating any
fixture.

**Test:** fixture asserting `rth_close_minutes_et(2026-01-19) == 780` and that
the 3 named trades reappear; a reconciliation test that fails if TV match rate
< 106/106.

**Gate blocked:** research authority, demo-observe, everything downstream.

---

## P0-2 — The cancel-confirmation discipline added to the exit path was NOT applied to the flatten path; two closing orders can coexist during a DLL flatten

**Severity:** **P1 on the certain part** (code-evidenced: the invariant the team
enforced on `result.exits` is not enforced on `flatten()`, so a stop fill and a
liquidation can both be live). **Escalates to P0** *if* Tradovate's
`/order/liquidateposition` opens a position when the account is already flat — in
which case the outcome is a reversed, unprotected short. That semantic is **not
documented, not tested in the repo, and not verifiable offline** (open question
#3); my probe assumes the adverse case by returning an orderId and filling it.
Stated separately per the evidence/inference rule.

**Files:** `src/full_python/tradovate/broker.py:265-294` (`flatten`), `:443-454`
(`_cancel_working_orders_best_effort`), vs the correct pattern at `:195-216` +
`:564-583` (`_ingest_cancel` → `_submit_pending_exit`).

**Invariant violated:** "two live closing orders must never coexist" — enforced
on `result.exits`, ignored on `flatten()`.

**Reproduction (executed):**
```
long open, protective stop 102 working
DLL breach -> flatten(): cancels=[{'orderId':102}], liquidation 103 submitted
   (liquidation submitted WITHOUT waiting for the stop cancel to confirm)
stop fill ingested        -> adapter position: None
liquidation fill ingested -> TradovateStateError: exit fill for order 103 while flat
                             => halt WITHOUT flatten
adapter believes: flat    REAL ACCOUNT: short 1, unprotected
```
The trigger is **correlated, not incidental**: the $1,000 DLL breaches on the
same adverse move that fills the stop.

**Consequence (certain):** during the DLL flatten the adapter can have a working
stop *and* a liquidation live at the same time — the exact condition it refuses to
allow on the strategy-exit path. **Consequence (conditional on liquidateposition
semantics):** an unmanaged reversed position with no protective order, while the
system halts believing it is flat.

**Fix:** flatten must use the same protocol — request cancel, wait for `Canceled`
(bounded), treat a stop fill as the close and suppress the liquidation; only
liquidate on confirmed-cancel or timeout, and then re-verify via
`/position/list` before submitting.

**Test:** `flatten → stop-fill-before-cancel-confirm ⇒ no liquidation order` and
`⇒ reconciled flat`. **Gate blocked:** demo-orders, paper, pilot.

---

## P1-3 — Live feed dies during the strategy's only trading window: forming-bar snapshots are counted as "ignored events"

**Severity:** P1
**File:** `src/full_python/tradovate/feed.py:105-124` (`next_bar`), `:73` (`max_ignored_events=100`)

Tradovate's chart stream **"sends updates on each tick, continuously updating the
currently-forming bar"** (official FAQ + community guidance). Each such update
produces no completed bar, so `next_bar` counts it as an ignored event and
raises `TradovateFeedError` after 100 of them **inside a single minute**.

**Reproduction (executed):** 120 snapshots of the same forming minute ⇒
`TradovateFeedError: Too many Tradovate chart events without a matching bar`.

The 09:30 open — the only minute the strategy trades — is the highest tick-rate
minute of the day. `TradovateFeedError` is neither `LiveDataError` nor
`ExecutionInvariantError`, so LiveLoop's halt protocol does not catch it; it
escapes to the runner's catch-all (with orders enabled: crash with an open
position and no flatten).

**Fix:** count only *unrecognised* events; a forming-bar update is progress.
**Test:** 500 same-minute snapshots then a newer minute ⇒ exactly one finalized
bar, no raise. **Gate blocked:** demo-observe.

---

## P1-4 — One early realtime snapshot silently discards the entire warmup history; the shadow gate cannot detect it

**Severity:** P1
**File:** `src/full_python/tradovate/feed.py:150-176` (`_accept_snapshot`)

Tradovate documents that chart bars **may arrive out of order and the client
must gather and sort them**, with an `eoh` (end-of-history) packet marking the
batch. `grep -i eoh` over the repo: **no matches** — `eoh` is not handled at all.
`_accept_snapshot` instead **drops any bar older than the current forming bar**,
with no error and no counter.

**Reproduction (executed):** one realtime snapshot for the current minute
arriving before the 240-bar historical batch ⇒ **all 240 warmup bars dropped, 0
emitted**. Strategy needs `warmup_bars=200`; it would see none and trade nothing.

**The gate cannot catch this** (also executed): `diff_bars` clips the reference
to `[captured[0], captured[-1]]`, so history missing from the *front* of the
capture yields **zero differences** — and the replay of the same truncated
capture also produces zero signals, so the verdict reads **PARITY + VERIFIED**
on a session that never warmed up.

**Fix:** buffer chart bars into a timestamp-keyed map until `eoh`; finalize by
max-timestamp-seen; raise on a pre-history bar after warmup. Additionally,
`diff_bars` must assert expected *coverage* (every reference RTH minute in the
session window present), not just agreement inside the captured range.
**Gate blocked:** demo-observe.

---

## P1-5 — `RECOVERY_REQUIRED` is set by normal, designed behavior, never cleared, and enforced by nothing

**Severity:** P1
**Files:** `broker.py:265-269` (`flatten` sets it unconditionally), `:536-537`
(only clears `execution_state` if `_recovery_required` is False — which it never
becomes), `:669-670` (property). `grep` shows **no reader anywhere in `src/`**.

**Reproduction (executed):**
```
routine $1,000 DLL flatten      -> execution_state = recovery_required
flat again, all orders settled  -> execution_state = recovery_required
NEXT SESSION (daily_limit reset)-> execution_state = recovery_required
new entry order placed while RECOVERY_REQUIRED? True
```
The state machine published in `docs/decisions/2026-07-12-phase0-correctness-remediation.md`
("Any open state → RECOVERY_REQUIRED → halt") is **advisory only**: LiveLoop never
reads `broker.execution_state`, and the flag latches permanently on the DLL
flatten the strategy is *designed* to perform.

**Fix:** distinguish `RECOVERY_REQUIRED` (halt, human ack) from `FLATTENED_OK`
(resume next session); make LiveLoop refuse to submit intents unless state is
NORMAL. **Gate blocked:** demo-orders.

---

## P1-6 — No event pump: the entire hardened order/fill lifecycle is unreachable in production

**Severity:** P1
**Evidence:** `grep -rn "ingest_raw_event|reconcile_rest_positions|execution_state" src/`
⇒ **zero hits outside `broker.py` itself.** Every Failure-Matrix-V2 test drives
`ingest_raw_event(...)` by hand (and `tests/test_tradovate_live_loop.py` injects
events from inside a `ScriptedBarSource`).

So the cancel-confirm exit path, protective-stop resubmission, reject/cancel
recovery, and position reconciliation are **component-level code with no
production caller**. The tests prove the components, not the system. The repo's
own doc admits this ("account-event pump" in Remaining Hard Blocks) — it should
be read as: the order path is not merely disabled, it is *not built*.

**Gate blocked:** demo-orders (this is the work item, not a bug to patch).

---

## P1-7 — The live broker applies no risk veto; it will submit orders the simulator refuses

**Severity:** P1
**File:** `broker.py:189-255` — no `RiskManager` call.

**Reproduction (executed), using the calendar's own MLK Day:**
```
MLK 09:35 ET: is_rth=False, rth_close=None
SIMULATOR (RiskManager) -> veto = 'market_closed'
LIVE BROKER             -> orders placed = 1
```
PaperBroker inherits `RiskManager` through `PositionEngine`; `TradovateBroker`
does not. The "identity by shared code" claim therefore covers the *paper* path
only. Divergence classes: `market_closed`, `outside_rth`, `after_flatten`,
`position_already_open`, `invalid_stop`, `invalid_quantity`.

**Fix:** call `RiskManager.veto_reason` in `apply_strategy_result` before
submission. **Test:** every veto reason ⇒ zero REST calls.
**Gate blocked:** demo-orders.

---

## P1-8 — No restart/recovery: the adapter starts flat and will double a live position

**Severity:** P1
**Files:** `live/runner.py:136-178` (composition root; no reconciliation),
`broker.py:129` (`_position = None` at construction).

Nothing calls `reconcile_rest_positions()` or `/fill/list` at startup. A restart
(crash, Ctrl-C, token failure) with a live position ⇒ adapter believes flat ⇒
next signal opens a **second** contract. Admitted in the doc; restated here
because it is the single largest remaining live hazard and there is no
`RECOVERY_REQUIRED` bootstrap state to block trading until reconciled.

**Fix:** at startup, GET `/position/list` + `/order/list`; if anything is open,
enter RECOVERY_REQUIRED and refuse to trade pending human ack.
**Gate blocked:** demo-orders, paper, pilot.

---

## P2 findings

- **P2-1 "Walk-forward" folds are all inside the original selection window.**
  `docs/decisions/2026-07-12-phase2-baseline-walk-forward.md` reports 7 "forward
  segments" 2023H1–2026H1 — but the TV-era config was *selected* on 2023–2026.
  The only genuinely pre-selection data (2021-03→2022-12, +$41,515 / 297 trades
  on the old calendar) is used as the un-scored "initial train" and is **omitted
  from the fold table**. Recommend: report it explicitly as the sole true OOS
  window, and label the rest "in-selection-era stability."
  (`research/walk_forward.py` performs no re-fitting — correct, since nothing is
  fit, but "walk-forward" overstates it.)
- **P2-2 Ablation has no significance test.** `docs/decisions/2026-07-13-phase2-component-ablation.md`
  ranks components by ΔP&L and fold counts with no paired-session t — the very
  statistic Gate 1 mandates. Conclusions (wings/prove-it load-bearing) are
  probably right and were correctly *not* promoted, but they are not gate-grade.
- **P2-3 No CI.** `.github/` absent; the 397-test suite runs only when someone
  remembers. Dirty-source guards exist in the research drivers but nothing
  enforces green tests on merge.
- **P2-4 Phantom dependencies.** `pyproject.toml` declares `numpy>=1.26`,
  `pandas>=2.2`; `grep -rnE "^\s*(import|from)\s+(numpy|pandas)" src/ scripts/`
  returns **0 matches**. Unpinned, unused, and an unnecessary supply-chain
  surface. No lockfile.
- **P2-5 Session-rollover halt on unconfirmed cancels.** `broker.py:156-165`
  raises if any order is still `working` at rollover. Because cancels are only
  marked on the `Canceled` event (which nothing delivers — P1-6), any flatten
  leaves orders `working` forever ⇒ guaranteed halt on the next session's first
  bar once the pump exists. Correct fail-closed instinct, wrong bookkeeping.
- **P2-6 Observe runner hangs on the mis-flagged holidays.** `runner.py:171-175`
  sets `calendar_end = end_minutes_et` when `rth_close_minutes_et is None`, and
  `bars_until` only checks the end time *after* a bar arrives. On MLK Day the
  market closes 13:00 but the runner waits to 16:05 with no bars ⇒ blocks until
  Globex reopens at 18:00.

## P3 findings

- **P3-1 Documented stop cap understates realized risk.** `max_stop_distance=31`
  caps distance from the *signal close*; the fill is next-bar-open + slippage.
  Measured over 829 trades: **395 (48%) exceed 31 pt; max 33.75 pt ⇒ $675/contract
  vs the documented $620.** The DLL projected-risk guard inherits the same basis.
- **P3-2 `entry_fill_rate` / `entry_delay_bars`** (research stress knobs) live in
  the production `SimulationConfig`. Defaults are safe (1.0 / 0); still, a
  research-only field that can silently skip entries belongs behind a separate
  scenario config.
- **P3-3 Broker commission defaults to 0.0** (`tradovate/config.py`) ⇒ live
  session P&L, and therefore the live DLL trigger, run commission-free unless set.
- **P3-4 `accounts[0]`** auto-selection in `runner.py:249` — harmless in GET-only
  observe mode, a wrong-account footgun the moment order wiring copies it.

---

## 1. Verdict by layer

| Layer | Verdict |
|---|---|
| Research (simulator arithmetic) | **Sound.** Traced long + short end-to-end; entry = next-bar open ± 0.75, P&L exact, stop frozen, stop-exit fills at stop ∓ slippage. All Phase 0/1/2 headline numbers reproduce **exactly** from raw data. |
| Research (baseline validity) | **Blocked by P0-1.** The authority baseline embeds an unregistered filter that fails the project's own materiality bar and broke TV parity 106/106 → 103/106. |
| Simulator | **Fit for purpose** once the calendar is corrected. MFE/MAE path-ambiguity work is a genuine improvement (0 → 59 flagged). |
| Demo observation | **NOT READY.** P1-3 kills the session at 09:30; P1-4 can void warmup while the gate prints PARITY. |
| Demo orders | **NOT READY.** No event pump (P1-6), no veto (P1-7), no recovery (P1-8), flatten race (P0-2). |
| Paper | **NOT READY.** Depends on all of the above. |
| Funded MNQ pilot | **Correctly rejected by the team's own gate.** Their analysis is sound and I endorse it. |
| Unattended production | **NOT READY, not close.** No CI, no alerting, no heartbeat, no recovery state. |

## 2. Claim ledger

| Claim | Status |
|---|---|
| Offline suite 371→397 passed | **VERIFIED** (397 passed, 4 skipped) |
| Corrected 5-yr NQ: 813 / $160,125 / -$18,570 / 59 ambiguous | **VERIFIED** — reproduced exactly |
| Corrected 5-yr MNQ: 859 / $25,931.50 / -$2,865.50 | **VERIFIED** — reproduced exactly |
| MNQ pilot flat-1: 859 / $12,554.50 / PF 1.326 / -$2,036 | **VERIFIED** — reproduced exactly |
| MNQ pilot fails the $500/30-session gate; pilot rejected | **VERIFIED and endorsed** |
| "$251K / PF 2.071 retired; Python is sole authority" | **VERIFIED** (Phase 1) — and correct |
| Bootstrap p95/p99 DD used for capital planning | **VERIFIED** — matches my independent bootstrap (NQ p95 ≈ -$41K) |
| Wings / prove-it are load-bearing (ablation) | **PARTLY VERIFIED** — direction credible, no significance test (P2-2) |
| "Phase 0 fixed holidays" | **CONTRADICTED** — introduced a wrong calendar (P0-1) |
| "Failure Matrix V2 28/28" | **PARTLY VERIFIED** — components pass; **nothing calls them** (P1-6) |
| "Cancel-confirmed exits; a stop fill wins the race" | **PARTLY VERIFIED** — true for `result.exits`, **false for `flatten()`** (P0-2) |
| Broker state machine NORMAL→…→RECOVERY_REQUIRED | **CONTRADICTED** — set on normal behavior, never cleared, never enforced (P1-5) |
| "Identity by shared code" (sim ≡ live) | **PARTLY VERIFIED** — holds for PaperBroker; TradovateBroker has no veto layer (P1-7) |
| Golden fixture proves TV parity | **CONTRADICTED** — fixture is sim-generated (circular); parity now 103/106 |

## 3. Adversarial failure matrix (20 incidents)

| # | Incident | Current behavior | Verdict |
|---|---|---|---|
| 1 | 09:30 tick storm, >100 forming snapshots | `TradovateFeedError`, escapes halt protocol | **FAIL (P1-3)** |
| 2 | Realtime snapshot precedes historical batch | 240 warmup bars silently dropped | **FAIL (P1-4)** |
| 3 | Warmup missing → no signals | Report prints PARITY + VERIFIED | **FAIL (P1-4)** |
| 4 | Stop fills during DLL flatten | Stop + liquidation both live; halt believing flat (reversal conditional on liquidateposition semantics) | **FAIL (P0-2)** |
| 5 | Stop fills during strategy exit | Close suppressed correctly | **PASS** |
| 6 | Exit order rejected | Emergency flatten + halt | **PASS** |
| 7 | Unsolicited stop cancel | Emergency flatten + halt | **PASS** |
| 8 | Flatten while entry in flight | Entry canceled; late fill → emergency flatten | **PASS** |
| 9 | `order_place` returns `failureReason` | Rejected event, no `KeyError` | **PASS** |
| 10 | Transport error mid-submission | `TradovateStateError` → halt | **PASS** |
| 11 | Partial fill | Halt (multi-contract submission blocked) | **PASS** |
| 12 | Fill for unknown order id | Halt without flatten | **PASS** |
| 13 | Duplicate fill | Halt | **PASS** |
| 14 | REST/WS position disagreement | Halt | **PASS (uncalled — P1-6)** |
| 15 | Process restart with open position | Adapter starts flat → doubles position | **FAIL (P1-8)** |
| 16 | Trading day the calendar calls a holiday | Sim/paper veto `market_closed`; live broker submits (no veto layer) | **FAIL (P0-1 + P1-7)** |
| 17 | Early-close day (13:15 futures close) | Modeled as 13:00; flatten 16 min early | **PARTIAL (P0-1)** |
| 18 | Routine DLL flatten | Latches RECOVERY_REQUIRED forever, gates nothing | **FAIL (P1-5)** |
| 19 | Cancel confirmation never arrives | Next session's first bar halts | **PARTIAL (P2-5)** |
| 20 | Data outage inside the window | Flatten + halt (broker authoritative) | **PASS** |
| 21 | DST transition | Correct both directions (verified on 4 dates) | **PASS** |
| 22 | Contract roll | TV-fitted rule, 22 rolls, position always flat overnight | **PASS** |
| 23 | Look-ahead | Truncation probe: decisions identical | **PASS** |
| 24 | Credentials in logs/artifacts | Env-only, repr-redacted, redacting probe; none in git history | **PASS** |

## 4. Missing tests (by risk)

1. Calendar: `rth_close_minutes_et(MLK) == 780`; the 3 named trades reappear.
2. Reconciliation regression: TV match rate must stay 106/106 (fails today).
3. Feed: 500 same-minute snapshots ⇒ 1 bar, no raise.
4. Feed: history-after-realtime ⇒ all history retained (or hard error).
5. Report: capture missing leading warmup ⇒ verdict ≠ PARITY.
6. Broker: flatten + stop-fill-before-cancel-confirm ⇒ no second closing order.
7. Broker: every `RiskManager` veto reason ⇒ zero REST calls.
8. Broker: state ≠ NORMAL ⇒ LiveLoop submits nothing.
9. Startup with an open broker position ⇒ RECOVERY_REQUIRED, no trading.
10. End-to-end WS→broker pump test with a fake socket (no such test exists).

## 5. Quantitative validity

The edge is **not** an artifact of one trade or one parameter — the team's own
ablation and my reproduction both support that — but it **is** era-dependent and
tail-carried, and the honest OOS evidence is thinner than the fold table implies.

- 5-yr NQ: 813 trades, $160,125, PF 1.420, WR 22.1%, observed DD -$18,570.
- Forward segments: 5/7 positive; **all of 2023 is a loss regime** (-$8,400).
- Top-10 trades ≈ 60% of net; net without top 5 = $102,785.
- Bootstrap (mine, independent): 5-yr 95% CI ≈ [$47K, $284K]; P(net ≤ 0) ≈ 0.2%;
  **maxDD median -$24K, p95 -$41K, p99 -$50K** vs -$18.6K observed. The team
  reached the same conclusion and now plans capital on p95/p99 — correct.
- **The load-bearing caveat nobody states:** the config was selected on TV data
  covering 2023–2026, so the 7 "forward" folds are in-selection-era. The only
  pre-selection window (2021-03→2022-12) is profitable and is *excluded* from the
  fold table. Verdict: **PLAUSIBLE BUT UNPROVEN**; only forward paper/live months
  can upgrade it.
- Survives cost stress: 4× slippage still leaves ~$78K/5yr (my earlier check);
  MNQ flat-1 survives doubled slippage. Not a cost artifact.

## 6. Remediation plan

**Phase A — restore research authority (blocks everything).**
Fix the calendar to per-day close times; re-run `reconcile` and restore 106/106;
regenerate the golden fixture only after parity is restored; re-run Phase 1/2/pilot
numbers on the corrected baseline; register the "skip abbreviated-holiday sessions"
question as a *candidate* through Gate 1 if it is still wanted (it fails at +$965).
*Exit:* TV match 106/106; corrected authority table published.

**Phase B — make demo-observe trustworthy.**
Feed: gather-and-sort until `eoh`; finalize on max-timestamp; stop counting
forming updates as ignored; map `TradovateFeedError` into the halt protocol.
Report: require full expected RTH coverage, not just agreement in-range.
*Exit:* 3 sessions, PARITY **and** full-coverage bar check, plus a deliberate
fault-injection session.

**Phase C — build the order path (not patch it).**
Account-event pump (WS `user/syncrequest` → `ingest_raw_event`); `RiskManager`
veto before submission; enforce `execution_state` in LiveLoop; separate
FLATTENED_OK from RECOVERY_REQUIRED; startup reconciliation via `/position/list`
+ `/order/list`; persistent client order ids; broker writes FILL/TRADE to the
ledger; apply cancel-confirm to `flatten()`.
*Exit:* Failure Matrix V2 re-run **through the pump**, plus a kill-9-and-restart
drill with an open demo position.

**Phase D — paper, then the 10-session operational pilot** exactly as the team's
own MNQ sizing decision specifies (flat 1 MNQ, $150/day, $500 cumulative, judged
on order integrity, not P&L).

## 7. Evidence required per promotion

- **Demo-observe:** 3 clean sessions with full-coverage bar verification against
  Databento; one induced feed-outage session showing halt+flatten.
- **Demo-orders:** every Failure Matrix row observed **through the real pump** on
  demo; restart-with-position drill passes; zero unvetoed submissions.
- **Paper:** 30 sessions, fill-level parity vs sim, slippage within model.
- **Pilot:** the team's own gate — and re-derived on the corrected calendar.

## 8. Do not proceed

- Do **not** enable `order_enabled` / `flatten_enabled` (P0-2, P1-5/6/7/8).
- Do **not** run observe sessions expecting a meaningful verdict until P1-3/P1-4
  are fixed — a green PARITY today can mean "never warmed up."
- Do **not** treat `golden_trades.json` as parity evidence.
- Do **not** judge any future candidate against the current baseline (P0-1).
- Do **not** raise the $500 pilot budget or the 10-session horizon.

## 9. Open questions (not answerable from the repo)

1. Tradovate's chart-update cadence per minute (drives P1-3's exact threshold).
2. Ordering guarantee between historical batch and realtime chart events (P1-4).
3. `/order/liquidateposition` semantics when already flat (determines whether
   P0-2 reverses the position or merely errors).
4. Whether the prop firm enforces an account-level DLL and whether it
   force-flattens — still unanswered; `userAccountAutoLiq` probe not yet run.
5. Whether the operator intends to trade the abbreviated holiday sessions at all
   (an economic choice — but it must go through Gate 1, not a "fix").

## 10. Final classification

**RESEARCH-ONLY** (and, strictly, *research-blocked* until P0-1 is fixed: the
current authority baseline is not the market).

Progress since 2026-07-12 is real and substantial — the broker's exit-path race,
reject/cancel recovery, placement-error mapping, multi-contract prohibition,
bootstrap risk, retirement of the $251K headline, the experiment registry, the
component ablation, and the honest rejection of the MNQ pilot are all genuine,
verifiable improvements, and the numbers reproduce exactly. But the Phase 0
"correctness" fix introduced a calendar that contradicts the exchange, deleted
TradingView-matched trades, and silently regressed the parity evidence the whole
engine rests on; the live data path has two session-killing defects; and the
hardened order lifecycle has no production caller.
