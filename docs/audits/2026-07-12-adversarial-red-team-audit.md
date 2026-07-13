# Adversarial Red-Team Audit: Full-Python NQ/MNQ System

Date: 2026-07-12  
Audited commit: `a64d9ae` (`claude/m4-regime`, equal to `origin/main` at audit time)  
Scope: all production packages and test categories, run artifacts, configs, scripts, README, HANDOFF, decision records, design specs, and the implementation plans relevant to current production behavior.  
Verification: `FULL_PYTHON_BASELINE_DATA=runs/baseline-anchor/nq1_2025-10-01_2026-06-26.csv python3 -m pytest -q` -> **354 passed**. The ordinary environment produced 350 passed and 4 skipped; the local anchor data was present, so the audit reran the skipped real-data checks.

This is a repository audit, not an assertion that any dormant order path has traded real money. `order_enabled` and `flatten_enabled` default false, and the only composition root is demo observe mode with orders impossible by construction. Execution findings describe what would happen if the dormant adapter were promoted unchanged.

## A. Executive Verdict

| Area | Score | Verdict |
|---|---:|---|
| Architecture | 6/10 | Good separation of strategy, simulation, risk, and adapters, but no recoverable account state model and two risk-value authorities. |
| Code correctness | 6/10 | Strong unit coverage; material MNQ configuration and excursion-sequencing errors remain. |
| Deterministic replay | 8/10 | Same process/data/config is deterministic and real-data identity tests pass; provenance does not include dirty source state. |
| Data integrity | 5/10 | Checks ordering/OHLC/symbol mix, but accepts 276 intra-session gaps including a 646-minute gap as structurally clean. |
| Backtest realism | 4/10 | One fixed bar model, static slippage, no missed fills/latency/impact, known close-timing divergence, and ambiguous MFE/MAE. |
| Statistical validity | 4/10 | Pre-registration discipline is useful, but there is no untouched final holdout, no selection-bias correction, and no confidence intervals in the platform. |
| Strategy coherence | 7/10 | A coherent opening momentum/S/R breakout thesis with positive historical results; causal contribution of many filters is not proven. |
| Market-awareness design | 3/10 | Measurement module exists; the only prospective gate failed holdout and is disabled. No live permission-state authority exists. |
| Research automation | 5/10 | Deterministic CLI and targeted grids exist; configs are not first-class artifacts and results are not queryable across trials. |
| Execution safety | 2/10 | Defaults are off, but the dormant enabled path has cancel/fill races, naked-position paths, and no idempotent recovery. |
| Observability | 3/10 | JSONL and HTML reports exist; there is no operational telemetry, causal linkage, account reconciliation loop, or alert taxonomy. |
| Live readiness | 1/10 | Demo observe tooling only. No successful observe-session artifacts, order-test gate, paper reconciliation, or restart recovery. |

The historical stack is profitable in the committed five-year artifact: 829 trades, $159,160 net, PF 1.412, 22.2% wins, and -$19,775 max drawdown. It is also fragile enough that the edge is not proven: 2023 lost $9,085, 29 of 64 months were negative, and the best five days/trades supplied 36.0% of net profit. A five-session moving-block bootstrap performed for this audit on the same in-sample daily series gave a 95% Sharpe interval of approximately 0.42-1.92. That interval is descriptive, not out-of-sample proof.

## B. Current Capability Boundary

**DETERMINISTIC REPLAY READY**

Justified:

- Historical canonical bars can be replayed deterministically through shared streaming indicators and the simulation engine.
- The local nine-month anchor reproduces the committed golden trades and paper-loop identity tests.
- Reports and event sequences are reproducible under a clean checkout.

Not justified:

- **SHADOW READY:** the runner exists, but the required three clean real demo sessions are not committed or otherwise evidenced.
- **PAPER READY:** no order-capable composition root, account-event consumer, recovery protocol, or end-to-end broker reconciliation exists.
- **LIVE READY:** P0 execution races and unprotected-position paths remain.

## C. System and State Diagram

### Actual historical flow

```text
Databento files / canonical CSV
  -> load_csv_bars (all rows materialized)
  -> validate_bars (gaps informational, other structural faults blocking)
  -> classify_timestamp (ET, CME date at 18:00)
  -> AdaptiveTrendStrategy
       streaming EMA/SMA/RMA/ATR/stdev/pivots/ATF/squeeze
       first failing gate OR accepted OrderIntent
       internal position/sizing/cooldown mirrors updated by fill/trade hooks
  -> PositionEngine (simulation position and fill authority)
       next-open entry/exit, frozen stop, bar OHLC stop handling, DLL
  -> EventLedger + Trade records
  -> daily/monthly/survivability/HTML reports
  -> manual promotion decision documents
```

### Actual observe flow

```text
Tradovate demo chart WebSocket
  -> TradovateMarketDataFeed
  -> LiveBarSource (contract check, active-window outage check)
  -> RecordingStrategy(AdaptiveTrendStrategy)
  -> TradovateBroker(order_enabled=False; no broker events/fills)
  -> PersistentEventLedger (flush per append, no fsync/hash chain)
  -> replay same bars through same strategy
  -> signal-list equality report
```

The observe comparison proves deterministic re-execution and catches recording corruption. It does not independently validate strategy semantics, fill behavior, broker state, or execution parity.

### Dormant enabled-order flow

```text
StrategyResult
  -> TradovateBroker REST order submission
  -> external code must call ingest_raw_event(...) for WS fills/cancels/rejects
  -> broker fill-derived position + FillPairingLedger
  -> LiveLoop drains synthetic BrokerEvents
  -> OrderStateMachine position shadow
  -> RiskSupervisor marks broker-local trades/position
```

There is no production component that subscribes to account/order events and feeds `ingest_raw_event`, periodically calls `reconcile_rest_positions`, restores state after restart, or blocks on recovery.

### State ownership and invariants

| Stage | Input -> output | Owned mutable state | Invariant/failure posture | Historical/live code identity | Proof |
|---|---|---|---|---|---|
| CSV loading | rows -> `MarketBar` | list of all bars | conversion exceptions; no schema version | Historical only | loader tests |
| Validation | bars -> quality report | counters | malformed OHLC/order/symbol fail closed unless `--allow-dirty-data`; gaps fail open | Historical only | validation tests |
| Session | UTC timestamp -> ET/CME session | none | fixed 18:00 boundary; no exchange calendar | Shared | DST/session unit tests |
| Indicators | bar -> feature state | rolling windows/state | deterministic; limited external Pine vectors | Shared strategy path | primitive unit tests + aggregate reconciliation |
| Strategy | features -> signal/intent/exit | trend, pivots, cooldown, position mirror, AM/DLL state | first failing gate only; no full feature snapshot | Shared | strategy/golden tests |
| Simulation | intents -> fills/trades | pending entry/exit, position, P&L | deterministic; bar-order assumptions | Paper uses same `PositionEngine`; real broker does not | simulation and identity tests |
| Risk manager | intent/context -> veto | immutable limits | fail closed for enumerated checks; instrument consistency not checked | Simulation only in current composition | risk tests |
| Tradovate broker | REST/WS events -> broker-local state | order map, position, stop id, fill ledger | claims fail closed; several asynchronous paths fail open | Separate from sim | fake-transport tests only |
| Event ledger | events -> JSONL | ordered list/counter | no causal refs, schema/version/hash, duplicate validation, or recovery | Formats shared, semantics differ | round-trip tests |
| Reporting | trades/events -> JSON/HTML | aggregation dictionaries | missing metrics and no trial correction | Historical/observe-specific | report tests |
| Promotion | reports -> decision | documents/manual process | human discipline, no machine-enforced registry | Research only | decision records |

The requested legal execution states are not implemented as a legal transition graph. `OrderStateMachine` models only flat/open position plus used order IDs. `ENTRY_PENDING`, `PARTIALLY_FILLED`, `EXIT_PENDING`, `HALTED`, and `RECOVERY_REQUIRED` are absent; order status exists separately in `TradovateBroker._orders`. Consequently impossible cross-object transitions are detected late, not prevented.

## D. P0/P1 Findings

### P0-01 - Stop cancellation is requested, not confirmed, before market exit

- **Severity/confidence:** P0 / high
- **Files:** `src/full_python/tradovate/broker.py:171-198`, `401-413`; `tests/test_tradovate_broker.py:376-397`
- **Evidence:** `_cancel_working_stop_or_halt()` treats a successful REST response as cancellation, and `apply_strategy_result()` immediately submits the market close. The test sends the cancel event only after both REST calls.
- **Trigger:** stop fills or cannot be canceled after REST accepts the cancel request but before the asynchronous cancel confirmation; market exit also fills.
- **Expected:** enter `EXIT_PENDING_CANCEL`, wait for definitive cancel, then submit one close; reconcile if status is unknown.
- **Current:** two live closing orders can coexist despite the comment claiming otherwise.
- **Consequence:** position reversal or excess short/long quantity.
- **Verification test:** fake REST accepts cancel; before cancel event, inject stop fill; then inject market-exit fill. Assert no second close was submitted, or state becomes `RECOVERY_REQUIRED` with account reconciliation. Current code submits both and the second fill raises only after exposure can already reverse.
- **Blocks:** paper, limited live, unattended live.

### P0-02 - Rejected exit or liquidation can leave a naked open position

- **Severity/confidence:** P0 / high
- **Files:** `src/full_python/tradovate/broker.py:171-198`, `235-257`, `440-452`
- **Evidence:** strategy exit cancels the stop first. `_ingest_reject()` performs emergency action only for `ROLE_PROTECTIVE_STOP`; rejection of `ROLE_EXIT` merely records `Rejected`.
- **Trigger:** strategy exit or `flatten()` cancels protection; broker rejects close/liquidation.
- **Expected:** immediately re-establish protection or issue independently reconciled emergency liquidation, then halt/recovery.
- **Current:** local position remains open, working stop may be gone, and processing can continue.
- **Consequence:** unlimited loss beyond configured risk.
- **Verification test:** enter long; confirm stop; request exit; confirm stop cancel; ingest reject for exit ID; assert an accepted protective stop exists or emergency flatten/recovery is active. Current code satisfies neither.
- **Blocks:** paper, limited live, unattended live.

### P0-03 - Unexpected protective-stop cancellation is accepted while open

- **Severity/confidence:** P0 / high
- **Files:** `src/full_python/tradovate/broker.py:454-459`
- **Evidence:** `_ingest_cancel()` clears `_working_stop_id` and emits `Canceled` regardless of role or open position.
- **Trigger:** exchange/broker/admin/manual cancellation of the protective stop outside a known exit transition.
- **Expected:** re-protect or emergency flatten and enter recovery.
- **Current:** position stays open with no protection and no halt.
- **Verification test:** enter and fill; ingest cancel for stop without an exit pending; assert fatal recovery plus re-protection/flatten. Current code continues.
- **Blocks:** paper, limited live, unattended live.

### P0-04 - Partial fills create real exposure that the local system treats as unknown/flat

- **Severity/confidence:** P0 / high
- **Files:** `src/full_python/tradovate/broker.py:270-273`; `src/full_python/execution/state_machine.py:45-49`; `src/full_python/execution/live_loop.py:108-121`
- **Evidence:** partial fill is queued without updating broker position or placing proportional protection; the state machine then raises and `LiveLoop` deliberately does not flatten on invariant errors.
- **Trigger:** entry market order partially fills.
- **Expected:** model partial quantity, protect filled quantity, cancel remainder, reconcile account, then flatten or continue under explicit policy.
- **Current:** broker may hold an unprotected partial position while local `broker.position` is flat; loop halts without flatten.
- **Consequence:** unmanaged live exposure.
- **Verification test:** submit quantity 2; ingest partial fill quantity 1/remaining 1; assert broker position=1 and protective stop qty=1 before halt. Current broker position remains `None`.
- **Blocks:** paper, limited live, unattended live.

### P0-05 - Order submission has no idempotency or unknown-result recovery

- **Severity/confidence:** P0 / high
- **Files:** `src/full_python/tradovate/broker.py:188-225`, `242-257`; `src/full_python/tradovate/http.py:138-168`
- **Evidence:** no client order ID is generated/persisted before submission. A broker-accepted request followed by HTTP timeout/error leaves an unknown order that is absent from `_orders`. Duplicate response order IDs overwrite existing entries without validation.
- **Trigger:** TCP loss after broker acceptance, process crash between POST and map update, or retry after uncertainty.
- **Expected:** persist logical intent/client ID before send; query broker by ID on uncertainty; exactly-once logical submission.
- **Current:** unknown live order, later classified as manual/unknown; a retry can duplicate exposure.
- **Consequence:** duplicate or orphaned orders/positions.
- **Verification test:** transport records an accepted order then raises timeout; restart and submit same intent. Assert one broker order and recovered mapping. No mechanism exists.
- **Blocks:** paper, limited live, unattended live.

### P1-01 - MNQ research silently uses NQ projected-risk dollars

- **Severity/confidence:** P1 / high
- **Files:** `src/full_python/strategy/adaptive_trend_config.py:53-56`, `283-296`; `src/full_python/cli.py:134-139`; `tests/test_sizing_candidates.py:16-68`
- **Evidence:** `production_am_config()` fixes `dollar_point_value=20`; setting simulation `point_value=2` does not modify or validate it. The committed `runs/sizing-5yr/1_MNQ/report.json` contains strategy value 20 and simulation value 2.
- **Trigger:** any `adaptive_trend_am` MNQ run, including CLI defaults unless the strategy config is separately replaced.
- **Expected:** one immutable instrument specification controls tick, point value, commission, symbol root, risk, broker, and reports; construction fails on disagreement.
- **Current:** projected DLL sizing evaluates each MNQ stop as NQ risk while realized/unrealized P&L uses MNQ dollars.
- **Consequence:** incorrect entry permissions/quantity and invalid MNQ-vs-NQ conclusions.
- **Verification test:** construct MNQ run and assert `strategy.dollar_point_value == simulation.point_value == 2`; then assert a 30-point stop risks $60 per MNQ. Current committed run fails the first assertion and computes $600 in `_dll_safe_quantity`.
- **Blocks:** MNQ research conclusions, paper, live.

### P1-02 - Run IDs do not identify the executed source tree

- **Severity/confidence:** P1 / high
- **Files:** `src/full_python/cli.py:71-83`, `141-150`
- **Evidence:** `_code_version_hash()` stores only `git rev-parse HEAD`; dirty tracked and untracked source changes are ignored.
- **Trigger:** run an experiment, edit strategy code without commit, rerun same data/config.
- **Expected:** refuse dirty tree or include source-tree hash and dirty flag/diff hash.
- **Current:** same run ID can represent different code and results.
- **Consequence:** irreproducible promotion evidence and artifact collisions.
- **Verification test:** monkeypatch a source file in a temporary Git repo without committing; assert run ID changes or execution refuses. Current ID remains the same.
- **Blocks:** promotion-grade research and deterministic provenance, not basic replay.

### P1-03 - The event ledger cannot reconstruct or recover account state

- **Severity/confidence:** P1 / high
- **Files:** `src/full_python/events.py:23-84`; `src/full_python/live/persistence.py:21-46`; all broker order methods
- **Evidence:** events have sequence-local IDs only, no schema version, run ID, causation/correlation IDs, logical intent ID, broker order ID linkage, account snapshot, state version, checksums, or hash chain. `flush()` is used without `fsync`; partial trailing records are not recovered. Restart intentionally starts a new file and does not replay state.
- **Trigger:** crash after submit/before ack, partial write, duplicate event, restart with broker position.
- **Expected:** replayable state transitions with duplicate/missing detection and broker reconciliation before entries.
- **Current:** JSONL is a useful trace, not a financial audit ledger.
- **Consequence:** phantom flat/open state and inability to prove protection or exactly-once behavior.
- **Verification test:** crash between intent persistence/submission/ack at every boundary; recover from ledger+broker snapshot; assert identical state and no new entries until reconciled. No recovery API exists.
- **Blocks:** paper, live, unattended operation.

### P1-04 - Data gaps fail open and the exchange calendar is incomplete

- **Severity/confidence:** P1 / high
- **Files:** `src/full_python/data/validation.py:67-78`, `120-124`; `src/full_python/data/sessions.py:39-69`
- **Evidence:** the five-year report labels data structurally clean despite 276 intra-session gaps and a maximum gap of 646 minutes. Session logic knows weekdays and an 18:00 boundary, not holidays or shortened sessions.
- **Trigger:** missing bars during indicator warmup/trade lifecycle; half-day close; holiday/weekend edge cases.
- **Expected:** classify expected exchange breaks with a calendar; quarantine unexplained gaps by session; prohibit promotion runs with active-window gaps.
- **Current:** all gaps are informational and replay continues.
- **Consequence:** altered indicators, skipped stops/exits, false sessions and misstated daily statistics.
- **Verification test:** delete a 09:40 ET bar before a signal and assert run is invalid, not merely reported. Current validation permits it.
- **Blocks:** promotion-grade research when gaps intersect active state; does not block smoke replay.

### P1-05 - MFE/MAE can include price reached after the trade was already stopped

- **Severity/confidence:** P1 / high
- **Files:** `src/full_python/simulation/position_engine.py:196-236`; `src/full_python/tradovate/ledger.py:78-87`
- **Evidence:** full-bar excursions are updated before stop/target processing. With OHLC bars and no target, a stopped long can receive the bar high as MFE even if the low hit the stop first; it is not marked ambiguous.
- **Trigger:** any bar containing both favorable excursion and the stop.
- **Expected:** tick/lower-timeframe sequence, or bounded/unknown excursion values; mark path ambiguity.
- **Current:** MFE/MAE are presented as exact.
- **Consequence:** invalid MFE gates, exit-efficiency analysis, and counterfactual conclusions.
- **Verification test:** long at 100, stop 95, bar O=100 H=120 L=94 C=110. Current trade records MFE=20 even under the engine's own worst-case stop-first policy; expected MFE is unknown or bounded 0-20.
- **Blocks:** MFE/MAE research claims, not basic P&L replay.

### P1-06 - Live broker/account authority is not connected end to end

- **Severity/confidence:** P1 / high
- **Files:** `src/full_python/live/runner.py:207-270`; `src/full_python/tradovate/broker.py:266-283`, `469-511`
- **Evidence:** the only runner connects the market-data WebSocket and constructs an orders-disabled broker. No component subscribes to account/order events, calls `ingest_raw_event`, periodically reconciles REST positions, or starts from broker truth.
- **Trigger:** promotion to an order-capable runner by toggling configuration or copying the composition root.
- **Expected:** account-event stream, startup reconciliation, heartbeat, token/stream renewal, state recovery, and entry gate.
- **Current:** the broker adapter is an offline-tested library, not an executable broker system.
- **Consequence:** fills would not update position/trades/risk.
- **Verification test:** end-to-end demo order through real or protocol-faithful mock server from intent to account fill to position to stop to close to reconciliation. Missing.
- **Blocks:** paper, live, unattended operation.

### P1-07 - Account risk authority excludes manual/platform/account-wide state

- **Severity/confidence:** P1 / high
- **Files:** `src/full_python/execution/supervisor.py:52-100`; `src/full_python/tradovate/broker.py:146-163`
- **Evidence:** DLL and supervisor sum only strategy-paired local trades and local position marks. Unknown/manual/platform fills halt but are not incorporated; cash balance/account P&L is not authoritative.
- **Trigger:** manual intervention, platform liquidation, fees adjustments, another process/order, reconnect with preexisting state.
- **Expected:** broker account and position snapshots are sole truth; local strategy state is reconciled against them.
- **Current:** risk can understate account loss before the unknown event is processed, and there is no recovery.
- **Consequence:** daily limits may not protect actual account equity.
- **Verification test:** broker account reports realized loss while local ledger has none; assert entries disabled. No path feeds account P&L into the supervisor.
- **Blocks:** paper, live, unattended operation.

### P1-08 - Backtest viability is supported by one execution model, not a model range

- **Severity/confidence:** P1 / high
- **Files:** `src/full_python/simulation/config.py:11-57`; `src/full_python/simulation/position_engine.py`; `docs/decisions/2026-07-03-fill-simulation-policy.md`
- **Evidence:** only static next-open/signal-close timing and constant slippage are modeled. No missed fills, latency distribution, stop slippage by volatility, spread, market impact, delayed entry, or severe model exists. Known session-flatten semantics differ from TradingView on 7/106 matched trades and half-days.
- **Trigger:** opening volatility, gaps, news, thin liquidity, or delayed order acknowledgment.
- **Expected:** optimistic diagnostic, realistic base, conservative adverse, and severe plausible models with prospective thresholds.
- **Current:** “pessimistic” is asserted, not demonstrated.
- **Consequence:** reported expectancy/PF may not survive realistic execution tails.
- **Verification test:** full walk-forward report under at least four immutable execution configs, including missed/delayed fills and adverse stop gaps.
- **Blocks:** strategy viability and paper/live promotion.

### P1-09 - Statistical edge is plausible but not proven

- **Severity/confidence:** P1 / high
- **Files/artifacts:** `runs/multi-year-backtest/report.json`, `runs/sizing-5yr/summary.json`, Gate 1 decision documents
- **Evidence:** five-year PF is 1.412, one calendar year is negative, 45.3% of months are negative, and top five days/trades contribute 36.0%. The same 2025-07-01 onward “holdout” was consumed by the prior-vol candidate and is now repeatedly reported; no untouched final holdout remains. No Deflated Sharpe/PBO/SPA/Reality Check or registered trial inventory exists.
- **Trigger:** choose among strategies/filters after repeated trials, then assess on reused history.
- **Expected:** frozen untouched final holdout or prospective shadow period, trial registry, block-bootstrap intervals, and selection-bias correction appropriate to the number of tried variants.
- **Current:** positive historical evidence, not proof of persistence.
- **Consequence:** material risk that performance reflects regime luck/model selection.
- **Verification test:** prospective or untouched evaluation with pre-registered model and cost assumptions; same-sign expectancy with confidence interval above zero and acceptable tail/drawdown.
- **Blocks:** claims that the edge is proven; does not block continued research.

## E. Code-Correctness Findings

In addition to the P0/P1 items:

| ID | Sev. | Confidence | Location | Finding and exact verification |
|---|---|---|---|---|
| C-01 | P2 | High | `tradovate/broker.py:214-225`, `350-361`, `248-257` | Duplicate `orderId` responses overwrite `_orders` without rejection. Return the same ID for entry and stop; construction should halt before overwrite. |
| C-02 | P2 | High | `live/runner.py:54-67`, `215-221` | `--symbol-root MNQ` still sets NQ `$20/point`. Assert root MNQ yields point value 2 or reject the flag. |
| C-03 | P2 | High | `live/runner.py:256-261` | REST token renews, but the market-data WebSocket is not reauthorized with a renewed MD token. Force token expiry mid-session and assert data stream remains authenticated or halts with a ledger event. |
| C-04 | P2 | High | `tradovate/feed.py:82-83`, `147-151` | `_seen_timestamps` grows for process lifetime. Run multi-session feed and assert bounded retention. |
| C-05 | P2 | High | `events.py:75-84` | Reader accepts duplicate/out-of-order event IDs and does not detect a truncated trailing record. Add strict sequence/schema/hash validation and recoverable-tail policy. |
| C-06 | P2 | High | `live/persistence.py:40-42` | Flush is not durable against OS/power loss. Fault-inject after flush before close and require fsync/atomic checkpoint for financial events. |
| C-07 | P2 | Medium | `data/databento.py:195-200` | Duplicate timestamps are silently first-wins, even if OHLC differs. Feed conflicting duplicate rows and require a fatal conflict report. |
| C-08 | P2 | High | `cli.py:95-97`, `152-234` | Existing output directories are overwritten piecemeal; a failed rerun can mix artifacts. Write to fresh temp run dir and atomically finalize. |
| C-09 | P2 | Medium | `simulation/position_engine.py:559-578` | Supervisor `flatten_now` uses stale bar close in paper identity; real broker uses market liquidation. The identity test does not establish live price parity. |
| C-10 | P3 | High | `strategy/baseline.py:13-20` | Placeholder baseline is exposed as CLI default, making accidental non-production runs easy to misread. Require explicit strategy or label reports `PLACEHOLDER`. |

Indicator semantics status:

| Primitive/behavior | Status | Evidence |
|---|---|---|
| EMA, SMA, RMA, ATR, true range, population stdev, rolling extrema, linreg endpoint | **PROVISIONALLY MATCHED** | Synthetic unit vectors validate formulas, but no independent Pine-exported primitive golden vectors. |
| Pivot confirmation/tie/shift/fixnan | **PROVISIONALLY MATCHED** | Synthetic ties plus documented real trade reconciliation; no full per-bar primitive export. |
| ATF trend transitions | **PROVISIONALLY MATCHED** | Synthetic transition test and matched entries, not per-bar Pine values. |
| Squeeze value/release/momentum color | **PROVISIONALLY MATCHED** | One trending-tape test and aggregate entry parity, not edge/equality vectors. |
| Breakout/prove-it/wings/cooldowns/frozen stops | **PROVISIONALLY MATCHED** | Strategy tests and exact matched entries on 106 TV trades. |
| Full Pine exit semantics | **MISMATCHED** | 93/106 exact exit timestamps and 13/106 raw exit-reason labels; 7 normal flatten exits and one half-day have documented price/time mechanics differences. |
| NaN propagation, one-tick equality edges, simultaneous multi-transition bars | **UNTESTED** | Current tests do not provide exhaustive Pine vectors. |

No direct look-ahead was found in the implemented Adaptive Trend entry path. Pivots confirm right bars late and return the prior held value. This does not elevate primitive parity to PROVEN.

## F. Trading-Logic Findings

### Coherent logic

- Pivot-confirmed S/R breakout plus prove-it targets continuation after a level is accepted, not merely touched.
- ATF and moving averages express directional alignment.
- Squeeze/momentum and wings target expansion-quality bars.
- Frozen stops, explicit cooldowns, and a narrow opening window are understandable risk/market-structure choices.
- Both long and short sides contributed over five years ($96,355 and $62,805).

### Unproven logic

- No component-level ablation table establishes incremental expectancy after costs for ATF, MA50, MA200, squeeze release, squeeze acceleration, wings, prove-it, and cooldowns.
- The exact causal persistence of the 09:30-10:00 window is plausible but not externally established.
- Dynamic S/R stop economics are partially sensitivity-tested, not proven as superior to a simpler volatility stop out of sample.
- Anti-martingale is a separate path-dependent strategy. The committed headline five-year run uses it; a clean flat-size signal-edge scorecard is not the headline artifact.

### Contradictory logic

- Project goals say MNQ-first risk validation; handoff recommends 1 NQ if drawdown permits; code silently prices MNQ projected risk as NQ. This is an unresolved instrument-policy contradiction, not a mere documentation issue.
- The system says safety rails should be independent, but the strategy DLL and projected-risk guard alter trade selection and quantity. Headline results therefore combine signal edge, sizing, and risk controls.

### Redundant or overlapping logic

- ATF, MA50/MA200, squeeze momentum, wings, and prove-it are all directional/expansion filters. Their conditional overlap and marginal contribution are not reported.
- Rejection logs preserve only the first failing gate, so overlap cannot be measured from the ledger.

### Overfit risk

- Exact thresholds (12/22/4.5 ATF, 5/3 pivots, 2-bar prove-it, 0.40/0.65 wings, 15/31 stop, precise time window, three cooldowns) create a large implicit search space.
- Nearby MA and S/R grids were sensibly closed without promotion, which lowers but does not remove selection risk inherited from the TradingView era.
- The purported `$251K / PF 2.071` TradingView production record is not reproducible from a committed three-year export. The committed Python five-year artifact is materially weaker at PF 1.412.

### Falsifiers for the premise

- Flat-size, no-DLL signal edge loses after conservative execution costs.
- Profit disappears outside the top five days or in prospective data.
- Adjacent timing/stop/ATF settings show a narrow isolated optimum.
- Contract-specific replay or tick ambiguity treatment removes the right tail.
- Live shadow feature distributions or fill/slippage differ materially from research assumptions.

## G. Backtest and Statistical Findings

Metrics currently trustworthy as deterministic summaries of the implemented model:

- trade count, arithmetic net/gross P&L, fixed commission, model-defined drawdown/loss streak;
- long/short and exit-reason decompositions;
- data/config hashes except dirty source provenance;
- exact matched entry timestamps/prices for the documented 106-trade TradingView overlap.

Metrics that are conditional or misleading without qualification:

- **Sharpe:** point estimate has no confidence interval and follows a selected strategy history; it is not Deflated Sharpe.
- **MFE/MAE/exit efficiency:** path-ambiguous on stop bars.
- **Profit factor/expectancy:** valid for one static fill model, not a tradability interval.
- **Maximum drawdown/loss streak:** one historical path; no bootstrap/risk-of-ruin distribution.
- **Best-trade dependency:** only top one is in standard reports; top five is 36% and must be standard.
- **Parity:** entry parity is strong; full exit mechanics are not exact.
- **MNQ sizing:** invalid until point-value authority is fixed and rerun.

Appropriate statistical methods now:

1. Session-level moving-block bootstrap for expectancy, Sharpe, max drawdown, loss streak, top-day dependency, and annual net. Trades are clustered by opening regime/day; IID trade bootstrap is inappropriate.
2. Deflated Sharpe or a comparable trial-count adjustment once every historical candidate/trial is registered.
3. White Reality Check or Hansen SPA for a finite, predeclared family of correlated parameter/strategy variants. Do not apply it after pruning undocumented trials.
4. CSCV/PBO only when a sufficiently broad, consistently evaluated candidate matrix exists; current ad hoc historical branches make an immediate number misleading.
5. Anchored/rolling walk-forward plus a new untouched/prospective holdout. The old holdout has been consumed.

## H. Market-Awareness Assessment

Current status: measurement only. `regime.py` computes ADX, variance ratio, gap, overnight range, and prior volatility tags. The prior-vol permission gate was correctly rejected after holdout sign reversal and defaults off. No evidence supports a live regime classifier.

Minimum viable permission framework:

```text
NORMAL
REDUCED_RISK
NO_NEW_ENTRIES
HALTED
RECOVERY_REQUIRED
```

- `NORMAL`: data/broker/event health good; static validated strategy only.
- `REDUCED_RISK`: operational slippage/fill degradation or volatility threshold validated prospectively; MNQ/quantity reduction only.
- `NO_NEW_ENTRIES`: scheduled event/holiday/roll/data freshness/account mismatch; preserve existing protection.
- `HALTED`: deterministic safety breach with broker state known; cancel entries, manage/flatten under explicit policy.
- `RECOVERY_REQUIRED`: any unknown order/position/protection state; broker reconciliation and human acknowledgment required.

First candidates should be rule-based and causal: scheduled event windows, exchange calendar/half-days, roll proximity, stale/incomplete bars, broker/account mismatch, and observed slippage/fill degradation. Market-regime features should remain measurement-only until adjacent thresholds, yearly stability, avoided-loss/forfeited-profit, and prospective false-shutdown rates are reported. Machine learning is not justified.

## I. Research Platform Assessment

Strengths:

- deterministic bar-by-bar strategy path;
- pre-registered train/holdout decisions and paired session comparisons;
- single-axis and two-axis grids;
- complete trade/event artifacts for committed headline runs;
- named rejected candidates and closed research branches.

Material gaps:

- `configs/` is empty; configurations live in Python constructors and scripts.
- no immutable experiment registry, parent experiment ID, trial count, environment/dependency lock, or dirty-source hash;
- no random/constrained search, walk-forward fold orchestrator, execution-scenario matrix, or cross-experiment query store;
- no automated isolated-gate or sequential-funnel attribution;
- rejected signals contain first failure only and no feature snapshot/hypothetical lifecycle;
- report generation omits many required risk/quality metrics;
- broad trial history from the Pine era is not machine-countable, preventing honest selection-bias correction.

Suitable storage at this scale: immutable YAML/JSON config and manifest per run plus a single SQLite index containing experiments, trials, datasets, configs, metrics, gates, and artifact paths. Keep large trades/events as Parquet/JSONL files keyed by run ID. Do not add a service/database server.

Security/configuration findings:

- No committed broker credentials or tokens were found. Credential and token dataclass representations are redacted, and HTTP request/response representations redact known sensitive keys.
- Production execution is disabled by default and the current runner hardcodes demo observe mode, which is an appropriate fail-closed default.
- Dependencies use open-ended lower bounds (`numpy>=1.26`, `pandas>=2.2`, `pytest>=8`) with no lock file; a future dependency release can change numerical/runtime behavior under the same Git commit.
- No repository CI workflow is present. The strongest real-data tests depend on an operator-local file, so GitHub cannot currently enforce them.
- Configuration has no single instrument/account schema or precedence model. Python defaults, script overrides, and broker config can disagree, as the MNQ point-value defect demonstrates.

Operational reporting is also incomplete: there is no live heartbeat/data-age/clock-skew/order-latency/fill-latency/slippage/protection/account-position dashboard or severity-routed alerting. The minimum alert taxonomy should be `INFO`, `WARNING`, `ENTRY_DISABLED`, `CRITICAL`, and `EMERGENCY`, with protection loss, position mismatch, uncertain submission, and persistence failure requiring immediate human action.

Rejected-trade analysis must record all gate states and a candidate ID before sequential position/risk logic. Two views are required:

- **Isolated gate attribution:** hold the accepted-trade schedule fixed and evaluate one gate's candidate outcomes; label conflicts/position overlap unknowable.
- **Sequential funnel attribution:** replay gates in production order and report incremental acceptance/rejection; do not claim causal value for a late gate from raw rejected P&L.

## J. Missing Test Matrix

| Priority | Exact test | Expected safety property |
|---:|---|---|
| 1 | Stop cancel accepted but not confirmed; stop fills before exit submission | No market exit until cancel confirmed; no reversal. |
| 2 | Exit/liquidation reject after stop cancellation | Position re-protected or emergency recovery; never naked. |
| 3 | Unsolicited protective-stop cancel while open | Re-protect/flatten and enter recovery. |
| 4 | Partial entry/exit fills | Filled quantity represented and protected before halt. |
| 5 | POST accepted then timeout; restart | Exactly one logical order via persisted client ID and broker query. |
| 6 | Crash at every intent/submit/ack/fill persistence boundary | State recovers from events+broker; entries remain disabled until reconciled. |
| 7 | Broker position/manual trade/account P&L differs from local | Broker wins; `RECOVERY_REQUIRED`; risk uses account truth. |
| 8 | MNQ construction with strategy/sim/broker point-value mismatch | Constructor refuses; one instrument spec only. |
| 9 | Dirty source rerun | New provenance hash or refusal. |
| 10 | Active-window missing/duplicate/conflicting bars and DST/half-days | Invalid run or explicit worst-case handling. |
| 11 | Stop bar O=100 H=120 L=94 C=110, stop=95 | MFE is bounded/unknown, not exact 20. |
| 12 | Four fill models over walk-forward folds | Candidate must pass predeclared realistic/adverse gates. |
| 13 | Independent Pine primitive vectors for equality/ties/gaps/NaNs | Per-bar indicator parity status can become PROVEN. |
| 14 | Byte-identical event output plus changed data/config/source | Stable same-run identity; changed provenance differs. |
| 15 | Duplicate/missing/out-of-order/truncated ledger events | Corruption detected; valid prefix recoverable. |
| 16 | Three full demo observe sessions | Data completeness, exact prospective signal replay, failure artifacts. |
| 17 | End-to-end demo order lifecycle | Intent -> ack -> partial/full fill -> protection -> exit -> REST/WS reconciliation. |
| 18 | Exchange calendar/roll contract-specific fixtures | Correct session/contract/half-day behavior. |
| 19 | Top 1/3/5 trades/days, block bootstrap, drawdown breach | Confidence and outlier dependence are standard artifacts. |
| 20 | Rejected setup with multiple failing gates and overlapping trade | All gates preserved; counterfactual labeled valid/invalid/unknowable. |

Existing tests classify approximately as: extensive unit/regression tests, several integration tests around shared simulation/paper paths, golden-master real-data trades, and limited state-machine/fault tests using fakes. Missing are property-based tests, durable recovery tests, protocol-faithful broker end-to-end tests, reconciliation under real asynchronous races, and prospective live tests.

## K. Remediation Roadmap

### Phase 0 - Correctness blockers

- **Objective:** make current research internally valid and prevent known naked/duplicate-order paths.
- **Tasks:** unify instrument spec; fix excursion ambiguity; add explicit execution states; confirm cancel before close; handle exit reject/stop cancel/partial fills; persistent client IDs; dirty-source provenance; gap policy.
- **Required tests:** priorities 1-11 above.
- **Artifacts:** corrected MNQ rerun, failure-matrix v2, instrument schema, state-transition table.
- **Entry:** current commit/audit.
- **Exit:** no P0 paths; MNQ/NQ values cannot disagree; research runs reject dirty/unexplained active gaps.
- **Prohibited shortcuts:** toggling `order_enabled`; treating REST 2xx as final broker state; re-labeling partial fills as impossible.

### Phase 1 - Deterministic research authority

- **Objective:** prove simulator/indicator semantics and immutable provenance.
- **Tasks:** Pine primitive goldens, exchange calendar, contract-specific/roll manifests, strict ledger schema, atomic artifact finalize, flat signal-edge scorecard.
- **Required tests:** priorities 9-15 and full real-data CI fixture or checksum-controlled downloadable fixture.
- **Artifacts:** canonical instrument/data manifests, four fill-policy configs, flat and sized scorecards.
- **Entry:** Phase 0 complete.
- **Exit:** per-bar primitives proven or explicitly bounded; byte-identical clean runs; every artifact traceable.
- **Prohibited shortcuts:** aggregate P&L parity as primitive proof; continuous-contract-only conclusions.

### Phase 2 - Robust automated experimentation

- **Objective:** quantify selection risk and robustness without manual config drift.
- **Tasks:** declarative configs/spaces, SQLite trial index, walk-forward runner, execution stresses, block bootstrap, trial-count adjustment, all standard reports.
- **Required tests:** deterministic trial IDs, fold isolation, no holdout leakage, adjacent thresholds, top-day/trade removal.
- **Artifacts:** experiment registry, confidence intervals, stability surfaces, trial inventory.
- **Entry:** Phase 1 authority.
- **Exit:** one pre-registered candidate survives realistic/adverse execution and prospective/untouched validation.
- **Prohibited shortcuts:** selecting highest P&L; moving gates after seeing results; treating old holdout as untouched.

### Phase 3 - Market-state permissions

- **Objective:** add the least complex prospective abstention/risk system.
- **Tasks:** implement permission enum/state authority; begin with calendar/roll/data/broker health; prospectively test any market feature.
- **Required tests:** no future leakage, false shutdown rate, adjacent thresholds, avoided loss vs forfeited profit, state hysteresis.
- **Artifacts:** permission ledger and yearly/prospective attribution report.
- **Entry:** stable candidate and experiment registry.
- **Exit:** simple rules improve tail/survivability out of sample without erasing expectancy; otherwise retain static strategy.
- **Prohibited shortcuts:** online training, recent-P&L self-optimization, ML without baseline superiority.

### Phase 4 - Shadow trading

- **Objective:** prove prospective market data, state, and signal operation with zero orders.
- **Tasks:** run three or more complete demo sessions, add MD-token lifecycle, telemetry, strict bar/calendar checks, compare features/signals to offline replay.
- **Required tests:** outage, stale/frozen/out-of-order bars, token expiry, restart, contract roll, clock skew.
- **Artifacts:** append-only sessions, feature/signal diffs, data-quality and operational incident reports.
- **Entry:** Phases 0-3 complete.
- **Exit:** pre-registered clean-session count and no unexplained divergence.
- **Prohibited shortcuts:** counting empty/no-signal sessions as proof; order flags.

### Phase 5 - Broker paper integration

- **Objective:** prove complete asynchronous order and reconciliation lifecycle.
- **Tasks:** account WS consumer, startup/reconnect recovery, idempotent IDs, partial fills, protection monitor, REST/WS reconciliation, account P&L authority.
- **Required tests:** priorities 1-7 and 17 under protocol-faithful fault injection.
- **Artifacts:** intended-vs-actual orders/fills/slippage/latency/protection timeline.
- **Entry:** shadow gate passed.
- **Exit:** at least 30 paper sessions with zero unexplained state/protection incidents and quantified execution drift.
- **Prohibited shortcuts:** fake-only acceptance; manual reconstruction of missing events.

### Phase 6 - Limited live pilot

- **Objective:** smallest MNQ exposure under attended operation.
- **Tasks:** funded-account checklist, independent kill switch, broker/platform limits, alerting, incident drills, capped cumulative loss.
- **Required tests:** manual intervention, disconnect/restart, duplicate intent, rejected stop/exit, flatten failure, exchange halt.
- **Artifacts:** signed config, daily reconciliation, incident log, rollback evidence.
- **Entry:** paper gate and strategy validation; no P0/P1 execution blockers.
- **Exit:** predeclared session count/loss cap with no unexplained discrepancy.
- **Prohibited shortcuts:** NQ pilot first; unattended operation; increasing size to meet income goals.

### Phase 7 - Unattended-operation consideration

- **Objective:** decide whether autonomy is justified, not assume it.
- **Tasks:** redundant monitoring, durable recovery, operator paging, reconciliation SLA, disaster/credential rotation/runbook drills.
- **Required tests:** prolonged outage, host restart, corrupted ledger, stale token/data, broker disagreement, protection loss.
- **Artifacts:** operations SLOs, incident response records, external review signoff.
- **Entry:** successful limited pilot plus stable prospective edge.
- **Exit:** explicit human approval based on evidence.
- **Prohibited shortcuts:** live self-modification; silent reconnect/guessing; trading in `RECOVERY_REQUIRED`.

## L. Falsification Plan

Fastest, strongest attempts to refute viability:

1. Fix the MNQ point-value split and rerun all MNQ sizing artifacts. Reject prior MNQ conclusions if trade selection/quantity changes materially.
2. Run flat one-contract NQ and appropriately scaled MNQ with DLL/AM separated. If the flat signal edge is nonpositive under adverse costs, sizing did not create an edge.
3. Apply four predeclared fill models, including delayed/missed fills and volatility-linked stop slippage. Fail if realistic/adverse confidence intervals include unacceptable expectancy/drawdown.
4. Reconstruct path-ambiguous stop bars with tick data or worst-case bounds. Quantify P&L and MFE/MAE attributable to ambiguity.
5. Remove top 1/3/5/10 days and bootstrap by session blocks. Fail if acceptable annual expectancy/drawdown depends on a handful of days beyond the strategy's declared tail premise.
6. Walk forward by year/regime with a new prospective holdout. Fail on sign reversal or unstable execution-adjusted expectancy.
7. Compare contract-specific replay around every roll against the fitted continuous series. Fail if roll construction changes material signals/trades.
8. Run all Pine primitive equality/tie/gap vectors. Any unexplained per-bar mismatch reopens Python authority.
9. Execute protocol-faithful demo fault injection for cancel races, rejects, partial fills, and POST timeouts. Any unprotected or excess position blocks the broker path.
10. Shadow prospectively before market-state filtering. If realized feature, signal, or slippage distributions drift outside predeclared bounds, pause rather than adapt automatically.

## M. Required Final Statement

```text
CURRENT VERDICT:
DETERMINISTIC REPLAY READY; research platform is useful, but promotion-grade provenance, MNQ risk correctness, broker recovery, and live safety are incomplete.

STRATEGY EDGE:
PLAUSIBLE BUT UNPROVEN

RESEARCH AUTHORITY:
Historical replay is deterministic and entry parity is strong; MNQ sizing, MFE/MAE path analysis, full exit parity, and selection-adjusted statistical claims are not authoritative.

SHADOW-TRADING READINESS:
TOOLING BUILT, GATE NOT PROVEN; three clean prospective demo sessions and token/data fault evidence are absent.

LIVE-EXECUTION READINESS:
NOT READY; dormant adapter contains P0 cancel/reject/partial-fill/idempotency failures and lacks an account-event/recovery composition root.

UNATTENDED OPERATION:
PROHIBITED

TOP FIVE BLOCKERS:
1. Protective-stop cancel/market-exit race can create excess or reversed exposure.
2. Exit rejection or unsolicited stop cancellation can leave an open position unprotected.
3. Partial fills and uncertain submissions have no safe, idempotent recovery state.
4. MNQ projected-risk sizing silently uses NQ dollar risk, invalidating MNQ conclusions.
5. No untouched/prospective holdout or multi-model execution evidence proves the historical edge survives selection and live costs.

WHAT WOULD CHANGE THIS VERDICT:
Close every P0 with protocol-faithful asynchronous tests; unify and rerun instrument/risk configuration; make provenance and event recovery complete; prove primitive and contract/session semantics; then show a pre-registered candidate survives realistic/adverse execution, block-bootstrap uncertainty, and a genuinely untouched or prospective period before passing three shadow sessions and a reconciled paper campaign.
```
