# HANDOFF — start here

You are picking up an NQ/MNQ futures trading system (Python port of a
historically profitable TradingView strategy). This document is self-contained:
it does
not assume any particular AI tool, skill set, or external memory. Read it
fully before touching anything. Last updated 2026-07-14.

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

## 5. Current state (2026-07-13)

- **Historical replay correctness remediation — COMPLETE through the
  2026-07-13 audit follow-up.** Tradovate
  progressive bars now finalize correctly; shadow parity requires an
  independent bar CSV; exits wait for confirmed stop cancellation; reject and
  unsolicited-cancel paths enter explicit recovery; holiday/early-close logic
  is shared by sim/live; NQ/MNQ point value has one authority; RTH gaps fail
  closed; dirty source changes run identity; stop-bar MFE/MAE is bounded and
  flagged. Fill-time stop placement now fails closed, nonfinite bars and
  invalid execution costs are rejected, and the contaminated timing axis was
  rerun. See `docs/decisions/2026-07-12-phase0-correctness-remediation.md`
  and `2026-07-13-phase0-audit-follow-up.md`. This does NOT close the broker
  execution P0 findings in the 2026-07-13 principal audit.
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
  anchor. This is entry parity, not exact full-trade or broker parity.
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
  liquidation requests use exactly `accountId + contractId + admin`. This
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

Repository checkpoint: the 2026-07-13 principal audit used clean `main` at
`dce7988`; always verify current local and `origin/main` hashes rather than
trusting this prose checkpoint. Start new work from current `main` on a fresh
feature branch.

## 6. Open tasks (ranked)

1. **Run the attended Gate 5 DEMO evidence sessions.** The cold-start P1-03
   code blocker is fixed; now execute the outage/disconnect drills in
   `docs/live-observe-runbook.md`, then preserve three nonconsecutive clean
   sessions with independent reference bars, exact signal parity, risk probe,
   and redacted artifacts. A failed drill or unexplained session blocks the
   gate.
2. **Continue the order-capable broker redesign offline before any demo
   order.** Slice A closed duplicate-entry/feedback P0-02; Slice B removed
   unscoped position netting and corrected the liquidation schema; Slice C made
   logical submission intents durable and non-retryable while unknown. Next is
   Slice D: account user-event synchronization/startup hydration that resolves
   runtime identity, REST working orders, fills, protection, and prior journal
   history. Then implement broker-confirmed 15:59/shutdown flatten, partial
   quantities, and the complete adversarial failure matrix. P0-04, P0-05,
   P1-01, and the broader P1-02 remain open. See the 2026-07-14 broker-safe
   execution design.
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
