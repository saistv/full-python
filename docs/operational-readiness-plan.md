# Operational readiness plan — learning loops and robustness gaps

Date: 2026-07-19. Status: REFERENCE PLAN (not yet executed). Written at the
close of the offline broker backlog (PRs #27-#33), before the first Gate 5
demo session. Pick-up point for "make the system improve over time" and
"what a full-scale robust deployment still needs."

Owner context: operator asked for a system that improves with market-day
evidence rather than only emitting stat reports. This plan records what may
learn, what must never learn, and the ranked non-code gaps.

---

## 1. The learning principle (non-negotiable)

**The system learns FACTS. It never learns SIGNALS.**

The edge is a 21.7% win-rate, right-tail-dominated distribution (top 5 days
= 36% of five-year P&L; authority: 813 trades / $160,125 / PF 1.420). Any
feedback loop that tunes strategy parameters toward recent live results
mathematically sells the right tail to buy win rate — the same trade every
rejected exit modification and regime filter offered (30+ closed axes in
`docs/decisions/`). On ~150-170 trades/year, "adaptation" and "fitting
noise" are indistinguishable for years. Therefore: live adaptation of
strategy parameters is forbidden; every strategy change goes through the
worthiness standard / Gate 1 with pre-registered criteria
(`docs/specs/2026-07-17-automation-worthiness-standard.md`).

## 2. The four learning loops

### 2.1 Research loop — BUILT (keep feeding)
Pre-registered candidates, sealed trial registries, one-shot holdouts.
Rejections are banked knowledge (they permanently close ideas). Improvement
= ADDING validated uncorrelated sleeves (the AT + mean-reversion portfolio
vision), never mutating the working strategy. Live sessions feed this loop
by extending the dataset daily.

### 2.2 Cost-model loop — PARTIAL (best near-term investment)
Slippage, fill latency, opening spread, and calendar behavior are
measurable facts; updating them from evidence is safe and compounds into
every future backtest and research decision.
- Exists: reconciliation reports; modeled costs in the simulator
  (0.75pt slippage assumption, $10 RT commission).
- To build: a per-session **execution-quality record** (modeled vs. actual
  on every fill: slippage delta, latency, spread at signal) accumulating
  across paper/live sessions, plus a **quarterly pre-registered cost-model
  recalibration** procedure (measured update of simulator cost parameters;
  never touches strategy parameters).

### 2.3 Monitoring loop — NOT BUILT (most important missing piece)
Drift DETECTION, not adaptation: a standing statistical monitor answering
"is live still consistent with the backtest distribution?" with triggers
written BEFORE any drawdown:
- Rolling session P&L vs. the deterministic bootstrap bands (e.g.,
  30-session mean below the bootstrap p5 band -> halt and review).
- Drawdown vs. the -$42.5K p95 planning band.
- Execution quality vs. model (e.g., realized slippage > 2x modeled for 10
  sessions -> halt and review).
Purpose: the system tells the operator WHEN to think, so "is this normal?"
is never decided mid-drawdown. Live performance is the de-facto holdout
(no untouched historical holdout exists); this loop is how it gets scored.

### 2.4 Operational loop — BUILT (keep the ratchet)
Every incident becomes a test and a decision record (calendar incident ->
guardrail 0 + exchange fixture; phantom-position replay -> matrix row).
Continue through live operations: every real halt gets a post-mortem
decision doc and, where possible, a pinned regression test.

## 3. Robustness gaps, ranked

### 3.1 Before any funded session
1. **Alerting + watchdog.** Today a halt only writes a ledger entry. Need a
   push channel (ntfy/Telegram/email) for three events: HALTED,
   SESSION-DID-NOT-START by 9:28 ET, report verdict DIVERGENCE. Plus an
   EXTERNAL heartbeat check — a dead process with a position on is the one
   failure fail-closed code cannot see from inside. Buildable offline.
2. **Kill-criteria document.** Pre-committed stop/review rules: the §2.3
   statistical triggers + operational ones (N unexplained halts/week,
   broker or prop-firm rule change, account-breach proximity) + who decides
   + cooling-off. Stopping must be execution of a plan, not judgment under
   stress.
3. **Broker-outage runbook.** Manual flatten path when the VENUE is down
   with a position open: mobile app steps, trade desk phone number, account
   numbers on paper; rehearsed once on demo.
4. **Automation permission in writing.** Tradovate API terms for the
   account type AND the prop firm's automation policy, verified in writing.
   A rules violation zeroes an account faster than any drawdown.

### 3.2 Before scale
5. **Live contract-roll runbook.** Data rolls automatically; the LIVE order
   symbol (config exact-contract authority) changes quarterly — calendar
   reminder + rehearsed procedure + deliberate config update per roll.
6. **Hosting.** Always-on machine or VPS, NTP-synced, supervised launch
   that ALERTS instead of auto-restarting (startup flatten makes restarts
   safe; restarting stays a human decision).
7. **Backups beyond git.** Run artifacts, journals, session ledgers,
   experiment registries (the evidence trail) auto-synced off-machine.
8. **Data continuity.** Databento subscription; monthly dataset-extension
   routine; periodic fresh TV export so the parity test tracks reality.

### 3.3 Structural honesty / decisions
9. **No untouched holdout exists** — the edge is characterized, not
   independently validated; 2023 (both halves negative) is in-distribution.
   Mitigation = §2.3 monitoring with pre-registered bands.
10. **Prop-EV recompute** from the Python authority before buying more
    evals (April 2026 table used the retired $251K basis); check firm rules
    on running copies across stacked accounts.
11. **Research residue:** P2-1 (add the true pre-selection window
    2021-03→2022-12 to the walk-forward table) and P2-2 (ablation
    significance test) — cheap, closes the 2026-07-13 audit fully.
12. **Life logistics:** futures/prop tax treatment (professional advice);
    unattended-operation policy (backstop + DLL make unwatched days safe;
    which days it trades without the operator is a deliberate choice).

## 4. Proposed next offline build (when picked up)

One slice, spec -> plan -> TDD per the house method: **monitoring +
alerting + execution-quality ledger.**
- `live/execution_quality.py`: per-fill modeled-vs-actual record appended
  to each session dir; aggregation CLI.
- `live/monitor.py`: rolling-window checks against pre-registered limits
  (limits live in a committed config file, changed only by PR — same
  spirit as the Gate 5 literals).
- Alert hook: minimal outbound notifier for the three §3.1 events + an
  external heartbeat endpoint/file for a watchdog cron.
- Kill-criteria doc template committed alongside (`docs/kill-criteria.md`),
  filled in by the operator before the funded pilot.

## 5. Standing constraints (unchanged by this plan)

Nothing trades live until the Gate 5 chain passes; order/flatten literals
pinned False; strategy config frozen; every strategy change through the
worthiness standard; deferred multi-contract partial-fill lifecycle
required before AM-sized live (max 4). See HANDOFF §2/§6.
