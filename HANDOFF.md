# HANDOFF — start here

You are picking up an NQ/MNQ futures trading system (Python port of a
validated TradingView strategy). This document is self-contained: it does
not assume any particular AI tool, skill set, or external memory. Read it
fully before touching anything. Last updated 2026-07-10.

## 1. What this project is

`saistv/full-python` — an event-sourced, deterministic Python backtester
and (in progress) live-execution engine for the "Adaptive Trend"
strategy. The strategy is ALREADY VALIDATED and profitable; the work here
is (a) rigorously re-verifying it with fast Python tooling and (b)
building the machinery to trade it live. The Pine/TradingView version is
legacy reference only.

Goal: detailed reports, regime awareness, a mean-reversion sleeve,
smoother equity curve, good Sharpe, shorter losing streaks — but NOT at
the cost of the validated core, which no analysis this year has beaten.

## 2. Non-negotiable guardrails (violating these loses money or wastes weeks)

1. **No config change ships without ≥ $275,000 net P&L AND PF ≥ 2.071 on
   the same 3-year TradingView backtest.** No exceptions. The locked
   config sits at ~$251K / PF 2.071. Nothing has cleared the bar.
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
- **The test suite IS the executable spec.** `python3 -m pytest -q` →
  currently ~294 passed, 3 skipped. The 3 skips are real-data tests gated
  on `FULL_PYTHON_BASELINE_DATA` (the operator's local 9-month CSV); with
  it set, all pass and prove the live path reproduces the backtester
  trade-for-trade.

## 5. Current state (2026-07-10)

- **Baseline frozen & TV-reconciled** — Python engine matches TradingView
  106/106 trades at $0.00 entry-price delta on the 9-month anchor.
- **5-year dataset assembled** (2021-03-16 → 2026-06-26, 1.87M bars).
- **Gate 1 config audit COMPLETE — zero changes promoted.** Prior-vol gate
  rejected (holdout sign reversal); MA-length and S/R-interaction sweeps
  both closed (no cell cleared the bar). The locked config survived its
  full Python-era parameter audit unchanged.
- **Sizing settled (5-year):** trade **1 NQ** if the account absorbs
  ~$20K drawdown (best risk efficiency; the $1K DLL engages); use an MNQ
  stack only to fit a smaller account's DD budget, at ~18-22% worse
  Return/DD. See `docs/decisions/2026-07-06-sizing-gate-5yr.md`.
- **Live-engine sub-project 1 (execution core) — DONE, merged, real-data
  identity proven.** Broker-agnostic `LiveLoop`, `PositionEngine` shared
  by sim and live, `PaperBroker`, `RiskSupervisor`, order state machine.
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
- **Sub-project 3 — Tradovate adapter — offline-COMPLETE** (2026-07-10).
  Foundation (auth/HTTP/WS/feed/broker skeleton, Tasks 1-6) plus the
  gap-closure pass: all six tracked `TradovateBroker` safety gaps closed
  (fill-derived trade ledger, live DLL, broker-held frozen protective
  stop, cancel-then-close exit path, submitted-order map with
  halt-on-unknown/duplicate, position reconciliation). Broker Failure
  Matrix 28/28 (27 tested + 1 OCO row N/A-by-design). Specs:
  `docs/superpowers/specs/2026-07-07-tradovate-adapter-design.md` and
  `2026-07-10-tradovate-gap-closure-design.md`. Still offline-only —
  see guardrail 7.

Branch note: `claude/m4-regime` is the active integration branch; `main`
lags it. PR #13 consolidates it into `main`. Check open PRs before
assuming what is merged.

## 6. Open tasks (ranked)

1. **Merge PR #13** (`claude/m4-regime` → `main`: MR run-1 record + full
   Tradovate adapter incl. gap closure).
2. **Sub-project 4 — Gate 5/6/7 operational tooling:** demo observe →
   demo order test → paper → reconciliation → a tiny MNQ live pilot
   ($150/day, $500 total, 30 sessions). Slice 1 (Gate 5 observe runner)
   is BUILT — see `docs/live-observe-runbook.md`; next action is running
   the 3 observe sessions, then the demo-order-test spec. Includes
   dashboards; note that data_outage and invariant_violation halts share
   `transition="execution_halt"` and differ by the `reason` field —
   consumers must read `reason`.
3. **Resolve the account-level DLL open question** (see the Open
   Operational Decisions list in the adapter spec): does Tradovate/the
   prop firm enforce an account-level daily-loss limit, and does it
   force-flatten or only block new orders? Feeds sub-project 4's pilot
   checklist; the client-side DLL stays regardless.
4. **Mean-reversion sleeve — PAUSED** under its research contract after
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

**Realistic earnings (1 NQ, backtest, pessimistic cost model):** mean
month ~$2,500 but **median month ~$830**, ~45% of months negative,
lumpy/tail-driven. Judge annually (~$30K/yr per NQ over 5 years), not
monthly. On a capped prop account the realized take is lower (~$760/mo
net EV on the Select→Flex path) because daily caps clip the tail. Details:
`docs/decisions/2026-07-06-sizing-gate-5yr.md` and the monthly analysis in
the project history.

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
2. Tell it the branch: `claude/m4-regime` is active; check open PRs.
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
