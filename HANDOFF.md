# HANDOFF — start here

You are picking up an NQ/MNQ futures trading system (Python port of a
historically profitable TradingView strategy). This document is self-contained:
it does
not assume any particular AI tool, skill set, or external memory. Read it
fully before touching anything. Last updated 2026-07-19.

> **Start here.** The Milestone 2 broker redesign slices A-D2 are landed
> (PR #22-#27; D2 = the fail-closed incremental account-sync runtime with
> disconnect/liveness invalidation, token-client replacement, and periodic
> REST reconciliation). The July 17-18 research burst is recorded (PR #28):
> the permanent automation-worthiness standard is active, and all three new
> pre-registered candidates (opening-auction v1, level-retest v2,
> overnight-displacement v3) were REJECTED at frozen T1 with rescue
> forbidden. GitHub Actions now runs the offline suite on every push/PR
> (audit P2-3 closed; P2-4 phantom deps removed). Everything remains
> offline protocol evidence. Nothing may trade live: see
> `docs/audits/2026-07-13-adversarial-audit.md` for the open P0/P1 backlog.

## 1. What this project is

`saistv/full-python` — an event-sourced, deterministic Python backtester
and (in progress) live-execution engine for the "Adaptive Trend"
strategy. Its five-year historical result is reproducible and profitable
under the current model, but the edge is NOT independently validated: no
untouched final holdout remains and the complete historical trial family is
unknown. The work here is (a) prospective re-validation with fast Python
tooling and (b) building safe live machinery. The Pine/TradingView version is
legacy reference only.

Goal: detailed reports, regime awareness, a mean-reversion sleeve,
smoother equity curve, good Sharpe, shorter losing streaks — but NOT at
the cost of the frozen historical candidate, which no analysis this year has
beaten.

## 2. Non-negotiable guardrails (violating these loses money or wastes weeks)

0. **The exchange calendar is DATA, not strategy.** CME equity-index futures trade
   an abbreviated 09:30-13:00 ET session on seven US holidays (MLK, Presidents,
   Memorial, Juneteenth, Independence, Labor, Thanksgiving) — the entry window is
   open and the system trades them. Only Good Friday, Christmas and New Year are
   full closures. **Declining to trade a session the market is open for is a
   FILTER, and filters go through Gate 1 like anything else.** This guardrail
   exists because it was violated: a cash-equity calendar shipped as a "correctness
   fix", deleted TradingView-matched trades, and broke trade-level parity
   (106/106 → 103/106) undetected. Calendar rules are pinned to
   `tests/fixtures/cme_equity_rth_close.json` — 1,379 weekdays of the exchange's
   own record. Do not edit the calendar to match a belief; check the fixture.

1. **Python is the performance authority.** The historical
   `$251K / PF 2.071 / 448 trades` TradingView claim is unreproducible and
   must not be used as a baseline or promotion threshold. Candidate changes
   use the pre-registered Gate 1 conjunction against the corrected Python
   control on identical data and execution assumptions.
2. **Pre-registered evaluation only (Gate 1 protocol).** Lock the success
   criteria BEFORE running a sweep
   (`docs/decisions/2026-07-05-gate1-phase0-protocol.md`). If a result
   doesn't clear the pre-locked bar, the bar does not move — reject.
3. **One-shot holdout. A sign reversal on holdout fails the candidate
   regardless of how good train looked.** The cautionary tale:
   `docs/decisions/2026-07-05-prior-vol-gate-evaluation.md` — a filter
   that cleared EVERY train row and still reversed on holdout. Never
   promote on train-only evidence.
4. **The edge is the right tail.** ~21.7% win rate; a handful of big
   days/trades carry the P&L (top 5 days = 36% of 5-year total). Any
   filter or exit that clips winners to "improve" win rate or PF while
   reducing net P&L is NOT an improvement. Always check net P&L
   direction, report median alongside mean, and re-run with the top 1-3
   trades removed.
5. **Live safety = halt-and-flatten.** The live loop flattens + halts on a
   data outage (broker stays authoritative) and halts (without flatten) on
   an internal invariant violation (position unknown). Do not weaken this.
6. **Risk caps are PER-INSTRUMENT.** NQ = $20/pt, MNQ = $2/pt. A live NQ
   daily-loss cap must sit ABOVE the strategy's $1,000 DLL (~$1,500-2,000).
   $150/day is the MNQ pilot number — never set it on NQ.
7. **NOTHING TRADES LIVE YET.** The Tradovate adapter now exists
   (sub-project 3) but is offline-only: all tests run on fake transports,
   no credentials are wired anywhere, and `order_enabled` /
   `flatten_enabled` default False. Enabling them against a funded account
   is forbidden until the sub-project 4 gates (demo observe → demo order
   test → pilot checklist) are passed. Do not claim otherwise in either
   direction.

## 3. The working method (follow it; it is why the results are trustworthy)

Every feature goes: **brainstorm → written design spec → written
implementation plan (with complete code + tests) → implement task-by-task
with a review after each → whole-branch review → merge.** TDD (write the
failing test first), frequent commits, work on a branch/worktree never on
`main` directly. Research changes are pre-registered and gated as in §2.
Do not skip the review step, and do not merge red tests.

## 4. Where the authoritative records live (read these, not this file alone)

- **`docs/decisions/`** — the chronological research log. Every promote /
  reject / close decision with its evidence. THIS is the source of truth
  for "what was tried and why it was accepted or rejected." Read newest
  first.
- **`docs/superpowers/specs/`** — design docs per feature.
- **`docs/superpowers/plans/`** — implementation plans (complete code +
  tests) per feature.
- **The test suite IS an executable spec, not proof of broker parity.**
  `python3 -m pytest -q` currently passes, with operator-data tests gated on
  `FULL_PYTHON_BASELINE_DATA` (the local 9-month CSV). Those tests prove
  deterministic simulator/PaperBroker identity because both share
  `PositionEngine`; they do NOT prove the separate Tradovate lifecycle or
  account reconciliation path.

## 5. Current state (2026-07-14)

- **Exchange calendar corrected; TradingView parity restored (PR #25).** The
  2026-07-12 "holiday fix" applied a US **cash-equity** calendar to a **futures**
  market. CME equity-index futures trade an abbreviated **09:30-13:00 ET** session
  on MLK, Presidents, Memorial, Juneteenth, Independence, Labor and Thanksgiving —
  the 09:30-10:00 entry window is fully open, with real volume. Only Good Friday,
  Christmas and New Year are full closures. Declaring those seven days closed
  deleted trades TradingView had actually taken: trade-level parity silently fell
  to **103/106**, undetected because `golden_trades.json` had been regenerated
  from the engine's own output (a sim-vs-sim fixture cannot detect a
  sim-vs-TradingView regression). It also shipped an unregistered strategy filter
  worth **+$965 / 5yr** — an order of magnitude below the $10,000 materiality bar,
  so Gate 1 would have REJECTED it.

  Calendar rules are now pinned to **1,379 weekdays of the exchange's own record**
  (`tests/fixtures/cme_equity_rth_close.json`). Parity restored: **106/106, $0.00
  entry-price delta**, and now guarded by `tests/test_tv_reconciliation.py` rather
  than by a self-referential fixture. Corrected 5-yr authority (canonical replay
  after the same-day fill-time stop correction): **NQ 813 trades / $160,125 /
  PF 1.420 / observed max DD -$18,570** — see §7 for the bootstrap adverse bands
  and `docs/decisions/2026-07-13-phase0-audit-follow-up.md` for the canonical
  numbers (the calendar-correction doc's interim 829/$159,160 row was superseded
  the same day). Every qualitative conclusion of Phase 1 and Phase 2 survives,
  including the MNQ pilot's rejection. See
  `docs/decisions/2026-07-13-exchange-calendar-correction.md`.

- **Live market-data feed made protocol-correct (PR #25).** Three defects, each
  of which silently destroys a session, all reproduced-then-fixed: forming-bar
  snapshots were counted as "ignored events" (Tradovate updates the forming bar on
  every tick, so >100 snapshots in one minute killed the feed at the 09:30 open —
  the only minute this strategy trades); a single realtime snapshot arriving before
  the historical batch discarded **all** warmup history (`eoh` was not handled
  anywhere, though the protocol requires gather-and-sort); and `TradovateFeedError`
  escaped the halt protocol entirely. The shadow report now asserts bar
  **coverage** (warmup + full entry window), not merely agreement inside whatever
  range happened to be captured — a session in which the strategy could not have
  warmed up can no longer read as PARITY.

  **Supersedes the earlier claims** that "progressive bars now finalize correctly"
  and that "holiday/early-close logic is shared by sim/live" — both were wrong.

- **Historical replay correctness remediation (2026-07-12/13).** Exits wait for
  confirmed stop cancellation; reject and unsolicited-cancel paths enter explicit
  recovery; NQ/MNQ point value has one authority; RTH gaps fail closed; dirty
  source changes run identity; stop-bar MFE/MAE is bounded and flagged. Fill-time
  stop placement fails closed, nonfinite bars and invalid execution costs are
  rejected, and the contaminated timing axis was rerun. See
  `docs/decisions/2026-07-12-phase0-correctness-remediation.md` and
  `2026-07-13-phase0-audit-follow-up.md`. This does NOT close the broker execution
  P0 findings in the 2026-07-13 principal audit
  (`docs/audits/2026-07-13-adversarial-audit.md` — see its status table).
- **Phase 1 evidence migration — COMPLETE.** Standard reports now include
  deterministic session-block bootstrap bands and top-trade/day dependency.
  The old TradingView headline, old MNQ sizing verdict, and unsupported prop
  EV are retired. See `2026-07-12-phase1-evidence-migration.md`.
- **Phase 2 historical characterization — IMPLEMENTED, not independent OOS
  validation.** SQLite trial-budget
  registry and anchored fold reporting are built. Baseline walk-forward is
  positive in 5/7 NQ and 4/7 MNQ six-month folds; both halves of 2023 lose.
  The four-level NQ execution-cost axis also passes through 2 points per side
  ($120,010 net, PF 1.292, -$27,070 DD). See
  `2026-07-12-phase2-baseline-walk-forward.md` and
  `2026-07-12-phase2-execution-cost-axis.md`. After correcting fill-time stop
  invalidation, one-minute latency remains profitable at 804 trades, $159,935
  net, PF 1.437, -$19,735 DD, and 4/7 positive chronological segments. The
  combined latency-plus-10%-miss scenario is 733 trades, $142,520 net, PF
  1.424, -$16,090 DD, and 5/7 segments. Component ablation retained the frozen
  stack: wings and prove-it are
  strongly defensive, squeeze release is directionally useful, and the small
  aggregate gain without squeeze momentum is below the materiality bar. See
  `2026-07-13-phase2-execution-timing-axis.md` and
  `2026-07-13-phase2-component-ablation.md`. Intrabar bounds identify 65
  entry-minute stops and 59 path-ambiguous stop exits. Their P&L is fixed under
  the no-target stop-first model, but exact 5-20 point MFE-gate claims require
  sequence data. See `2026-07-13-phase2-intrabar-bounds.md`.

- **Baseline frozen & partially TV-reconciled** — Python matches all 106
  overlapping TradingView entries at $0.00 entry-price delta on the 9-month
  anchor. This is entry parity, not exact full-trade or broker parity. **It holds
  only with PR #25's calendar; before it, `main` reconciled 103/106.** The
  reconciliation is now guarded by a test against the operator's TV export
  (`FULL_PYTHON_TV_EXPORT`), not by a fixture the engine generated itself.
- **Automation-worthiness standard active; three candidates rejected at T1
  (2026-07-17/18, PR #28).** `docs/specs/2026-07-17-automation-worthiness-standard.md`
  is the permanent promotion contract for every new automated strategy: freeze
  the hypothesis, rules, costs, gates and trial budget before the first scored
  run; a failed gate consumes the trial and cannot be rescued by re-searching
  the same sample. Under it, opening-auction-regime v1, opening-auction
  level-retest v2, and overnight-displacement-reversal v3 each failed their
  frozen T1 primary trial and are CLOSED (v3's seven reconciliation flags were
  forensically shown to be evaluator false positives; the rejection stands and
  the sealed reports were not amended). Do not port, shadow, or re-run any of
  them. Verdicts in `docs/research/`.
- **5-year dataset assembled** (2021-03-16 → 2026-06-26, 1.87M bars).
- **Gate 1 config audit COMPLETE — zero changes promoted.** Prior-vol gate
  rejected (holdout sign reversal); MA-length and S/R-interaction sweeps
  both closed (no cell cleared the bar). The locked config survived its
  full Python-era parameter audit unchanged.
- **MNQ-first pilot sizing re-derived.** The old verdict is retired because it
  projected MNQ risk at NQ's $20/point while the simulator used $2/point. The
  flat-one-MNQ pilot candidate survives reference and doubled-slippage costs,
  but a 30-session funded pilot fails the `$500` cumulative-loss gate. The only
  retained funded path is at most 10 sessions after every operational gate;
  keep at least 30 sessions in paper/shadow. See
  `docs/decisions/2026-07-13-mnq-pilot-sizing.md`.
- **Live-engine sub-project 1 (simulation/paper orchestration) — DONE.**
  Broker-agnostic `LiveLoop`, shared simulator/PaperBroker `PositionEngine`,
  `RiskSupervisor`, and order-state shadow. Shared-code identity is proven;
  independent Tradovate execution parity is not.
- **Live-engine sub-project 2 (live data feed) — DONE, merged.**
  Vendor-agnostic `LiveBarSource`, contract authority, session-armed
  outage detection (halt+flatten). The trading window is config-driven
  and MALLEABLE (see §7).
- **Entry-window sweep — CLOSED, config unchanged** (2026-07-06). The
  9:30 open start is essential and irreplaceable; every later start is
  catastrophically worse. See
  `docs/decisions/2026-07-06-entry-window-sweep-closed.md`.
- **MR variant 2 (opening-range fade) run 1 — REJECTED** (2026-07-07,
  PF 0.692, t=-3.74). MR track paused per its research contract; run 2
  needs a new pre-filed mechanism, not parameter tuning. See
  `docs/research/2026-07-07-mr-orfade-run1-verdict.md`.
- **Sub-project 3 — Tradovate adapter skeleton — OFFLINE ONLY** (2026-07-10).
  Foundation (auth/HTTP/WS/feed/broker skeleton, Tasks 1-6) plus the
  gap-closure pass plus the 2026-07-12 adversarial remediation
  (fill-derived trade ledger, live DLL, broker-held frozen protective
  stop, cancel-then-close exit path, submitted-order map with
  halt-on-unknown/duplicate, position reconciliation). The principal audit
  found unresolved P0/P1 protocol and recovery defects, so "complete" is not a
  promotion claim. Broker Failure
  Matrix V2 is recorded in the Phase 0 decision. Specs:
  `docs/superpowers/specs/2026-07-07-tradovate-adapter-design.md` and
  `2026-07-10-tradovate-gap-closure-design.md`. Still offline-only —
  see guardrail 7.
- **Demo-observer cold-start remediation — IMPLEMENTED OFFLINE** (2026-07-13).
  Startup now arms from the injected clock, late/no first bars inside the
  active window produce a durable `data_outage` halt report, and flat no-data
  runs stop at the configured ET end. This closes principal audit P1-03 in
  code; the attended DEMO outage/reconnect drills and three clean sessions are
  still missing. See `docs/decisions/2026-07-13-demo-observer-cold-start.md`.
- **Broker authority foundation (Milestone 2 Slice A) — IMPLEMENTED OFFLINE**
  (2026-07-14). Simulation and live orchestration now dispatch one
  broker-produced strategy-feedback stream; Tradovate entry fills and closed
  trades reach the strategy exactly once, and entries are blocked unless the
  broker is stably flat. This closes principal-audit P0-02 in code only. See
  `docs/decisions/2026-07-14-broker-authority-foundation.md` and the staged
  lifecycle design in `docs/superpowers/specs/2026-07-14-broker-safe-execution-design.md`.
- **Broker identity authority (Milestone 2 Slice B) — IMPLEMENTED OFFLINE**
  (2026-07-14). Every flatten-capable adapter now requires one exact
  `contract_symbol` and `contract_id`; order decisions and broker position/fill
  events are account/contract scoped; ambiguous REST snapshots halt; and
  liquidation targets use exact `accountId + contractId + admin` identity.
  Slice D1 subsequently adds the documented `isAutomated` and correlation
  fields without changing that target authority. This
  closes P1-02's unsafe position-netting path and fixes the request-schema
  prerequisite for P0-04. The full P1-02 remains open until runtime identity
  resolution and REST working-order reconciliation are wired with startup
  hydration; P0-04 remains open until flat position and working-order state are
  confirmed after liquidation. See
  `docs/decisions/2026-07-14-broker-identity-authority.md`.
- **Durable order intents (Milestone 2 Slice C) — IMPLEMENTED OFFLINE**
  (2026-07-14). Every entry, stop, exit, cancel, and liquidation POST now has a
  hash-linked, `fsync`ed logical intent first. Explicit rejection and ambiguous
  outcomes are durable; timeout/malformed responses latch recovery; any prior
  journal history keeps a restarted broker closed; repeated cancellation and
  liquidation cannot submit twice. The older event log is now correctly named
  a best-effort observational trace. This is the persistence prerequisite for
  P0-05, not closure: account user sync must associate journal history with
  broker orders/fills before recovery. See
  `docs/decisions/2026-07-14-durable-order-intents.md`.
- **Stable-flat account startup hydration (Milestone 2 Slice D1) — IMPLEMENTED
  OFFLINE** (2026-07-14). Every order-capable broker now starts closed. An
  exact Tradovate `user/syncrequest` snapshot must agree with independent REST
  accounts, contract, positions, orders, commands, command reports, order
  versions, fills, cash balance, and account-risk state before entries reopen.
  Cash realized P&L is bound to the expected `tradeDate`; arbitrary statuses,
  malformed identities, foreign exposure, working orders, and source
  disagreement fail closed. New order/cancel/liquidation mutations carry a
  hash-covered broker-visible client ID, and acknowledged restart history is
  reconciled only through the same automated command and terminal order;
  accepted cancels require their exact terminal canceled order. Hydrated daily
  loss state also blocks entries before REST. This
  is stable-flat startup evidence only: inherited positions, unresolved
  outcomes, incremental sync, reconnect/token renewal, and periodic REST
  reconciliation remain open. See
  `docs/decisions/2026-07-14-account-state-hydration.md`.
- **Incremental account synchronization (Milestone 2 Slice D2) — IMPLEMENTED
  OFFLINE** (2026-07-15). Current exact-account `user/syncrequest` filters feed
  a strict atomic entity cache. Every property update, disconnect, shutdown,
  liveness failure, token replacement, malformed event, or REST disagreement
  closes broker authority. Fresh REST agreement can reopen only stable-flat
  state; renewal replaces both clients and requires full rehydration. The
  transport now exposes inbound activity and the client sends bounded
  application heartbeats. This does not close P1-01: the undocumented real
  split-response envelope, blocking-call heartbeat behavior, and attended DEMO
  reconnect drills remain unproven. See
  `docs/decisions/2026-07-15-account-sync-runtime.md`.
- **Confirmed flatten and session boundaries (Milestone 2 Slice E) — IMPLEMENTED
  OFFLINE** (2026-07-19). `flatten()` is staged and event-confirmed: journaled
  cancels first, liquidation only after every cancel confirms, then flat plus
  no-working-orders verified with a one-bar deadline (halt on anything slower
  or on a residual order). The P0-2 race — a protective stop filling before its
  cancel lands — resolves the flatten with the liquidation never submitted, so
  two live closing orders cannot coexist. Routine confirmed flattens end NORMAL
  with the DLL latch still blocking entries (P1-5's dead RECOVERY_REQUIRED
  latch removed); emergency semantics unchanged. The broker itself now triggers
  the flatten at exchange-calendar close minus one minute, including early
  closes (P0-03) — strategy-independent. P0-2/P1-5/P0-03 closed in offline
  code; P0-04's REST leg and the attended DEMO liquidation drill remain open.
  See `docs/decisions/2026-07-19-confirmed-flatten-session-boundaries.md`.

Repository checkpoint: the 2026-07-13 principal audit used clean `main` at
`dce7988`; always verify current local and `origin/main` hashes rather than
trusting this prose checkpoint. Start new work from current `main` on a fresh
feature branch.

## 6. Open tasks (ranked)

1. **Continue the order-capable broker redesign offline before any demo
   order.** Slices D1-D2 provide stable-flat startup plus incremental
   invalidation/reconciliation; Slice E provides the confirmed flatten and
   the calendar-driven session-close backstop. Next implement the production
   composition root / account-event pump (P1-6), the broker-side RiskManager
   veto (P1-7), restart/inherited-position recovery (P1-8), partial
   quantities, unknown-outcome recovery, and the complete adversarial failure
   matrix (Slice F). Capture the real DEMO split-sync envelope and prove
   heartbeat/reconnect behavior before composing any order runner. P0-04's
   REST leg, P0-05, P1-01, and the broader P1-02 remain open. See the
   2026-07-14 broker-safe execution design, the 2026-07-15 account-sync
   runtime design, and the 2026-07-19 confirmed-flatten decision.
2. **Run the attended Gate 5 DEMO evidence sessions.** The cold-start P1-03
   code blocker is fixed; now execute the outage/disconnect drills in
   `docs/live-observe-runbook.md`, then preserve three nonconsecutive clean
   sessions with independent reference bars, exact signal parity, risk probe,
   and redacted artifacts. A failed drill or unexplained session blocks the
   gate.
3. **MNQ-first sizing is re-derived; execute operational gates only after the
   blockers above.** A 30-session funded pilot fails the `$500` risk gate
   (23-27% budget-breach probability). The research conclusion is at most a
   10-session flat-1-MNQ funded operational pilot after demo observe, demo
   order, paper, and reconciliation pass; keep a 30-session paper/shadow
   record. See `2026-07-13-mnq-pilot-sizing.md`.
4. **Sub-project 4 - Gate 5/6/7 operational tooling:** demo observe -> demo
   order test -> paper -> reconciliation -> a tiny MNQ live pilot ($150/day,
   $500 total, at most 10 funded sessions). The observe runner exists and its
   cold-start blocker is fixed offline, but the real DEMO drills and the
   three-session evidence run remain open. Demo credentials and network access
   remain operator-controlled and must never be committed. Halt consumers must
   read both `transition="execution_halt"` and its `reason` field.
5. **Resolve the account-level DLL open question** (see the Open
   Operational Decisions list in the adapter spec): does Tradovate/the
   prop firm enforce an account-level daily-loss limit, and does it
   force-flatten or only block new orders? Feeds sub-project 4's pilot
   checklist; the client-side DLL stays regardless.
6. **Mean-reversion sleeve — PAUSED** under its research contract after
   the run-1 rejection; resume only with a new pre-filed mechanism.

## 7. Key facts a new agent will need

**Locked production config** (unchanged; `production_am_config()` in
`src/full_python/strategy/adaptive_trend_config.py`):

| Setting | Value |
|---|---|
| Instrument / size | NQ 1-min, 1 contract |
| Entry window | 9:30-10:00 ET (`entry_start_minutes_et`=570 / `entry_end_minutes_et`=600) — **config-driven, malleable, sweepable** |
| Backstop flatten | 15:59 ET |
| Stop | Dynamic S/R, max 31pt, minSR 15, srDist 5 |
| Sizing | Anti-martingale max 4; DLL $1,000 (equity-based) |
| Wings | body 0.40 / close 0.65 |
| Squeeze | momentum + release + accelerating, all ON |
| Trend filters | ATF sens 4.5 (len 12/22), MA50 + MA200 ON |

**Current planning evidence (corrected five-year 1 NQ backtest):** 813
trades, $160,125 net, PF 1.420, 22.1% wins, and observed max drawdown
-$18,570. A deterministic 10-session moving-block bootstrap gives annualized
net 95% CI of approximately $8.5K-$51.1K and max-drawdown median / p95 / p99
of about -$24.5K / -$42.5K / -$54.6K. Use the adverse distribution for
capital planning, not observed drawdown. No current prop-account monthly EV
is authoritative; it must be recomputed from explicit, current account rules.

**Repo orientation:** `src/full_python/` — `simulation/` (engine +
`position_engine.py`, the shared fill lifecycle), `strategy/`
(`adaptive_trend.py` + config), `execution/` (live loop, paper broker,
supervisor, state machine), `livedata/` (feed, contract authority, live
bar source), `live/` (observe-mode session runner, shadow report, risk
probe), `tradovate/transport.py` (real RFC 6455 client), `risk/`,
`data/` (loaders, sessions, databento continuous builder),
`research/sweep.py` (the Gate 1 sweep harness), `regime.py` (measurement
only — never gates entries).

## 8. How to actually do the handoff

1. Give the new agent access to the repo (`saistv/full-python`) and point
   it at THIS file first, then `docs/decisions/` newest-first.
2. Fetch and verify current `main` against `origin/main`, then create a fresh
   feature branch; do not rely on a commit hash copied from this handoff.
3. Have it run `python3 -m pytest -q` to confirm a green baseline before
   changing anything (set `FULL_PYTHON_BASELINE_DATA` to the local 9-month
   CSV to run the real-data identity/golden tests).
4. If it does not have the disciplined brainstorm→spec→plan→review skills
   built in, tell it to follow §3 manually — that discipline is why the
   results here are trustworthy.
5. The richest running notes live in the previous agent's private memory
   (outside this repo). Anything load-bearing from it has been distilled
   into this file and `docs/decisions/`; if something seems missing, the
   decision docs are authoritative.
