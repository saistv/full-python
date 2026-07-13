# Principal Adversarial Red-Team Audit: Full-Python NQ/MNQ System

Date: 2026-07-13

Audited commit: `dce798833a667f26e1efeebd795973cadb3557bb` (`main`, equal to local `origin/main`)

Starting document: `HANDOFF.md`

Safety boundary: offline only; no credentials requested or read; no broker connection; no order or liquidation request sent; no production source modified.

This audit treats passing tests, documentation, and historical profit as claims rather than proof. Findings describe dormant order-capable code only where explicitly stated. The current composition root is observe-only and cannot enable orders from its CLI.

> **Post-audit remediation note:** the follow-up change documented in
> `docs/decisions/2026-07-13-phase0-audit-follow-up.md` fixes P0-01, P1-04,
> and P2-03, and corrects the affected timing results. The remaining broker and
> operational findings stay open, so the final `RESEARCH-ONLY` classification
> is unchanged.

## 1. Findings

### P0-01 - A delayed fill can put the planned stop on the profitable side of entry

- **Severity:** P0 (false performance); high confidence.
- **Files:** `src/full_python/risk/risk_manager.py:78-85`; `src/full_python/simulation/position_engine.py:181-210`, `511-564`.
- **Violated invariant:** a long stop must be below the actual long fill and a short stop must be above the actual short fill.
- **Evidence:** the risk manager validates the stop against the signal/reference price. A pending entry can fill at a later bar open, but `_open_position()` never validates or recomputes the stop against that fill. The same bar can then close at the now-profitable stop.
- **Reproduction:** an offline two-bar strategy was given a long signal at 100 with stop 90 followed by an open at 80. It booked entry 80, stop exit 90, and +10 points. The mirrored short at 100, stop 110, next open 120 also booked +10. On the five-year `entry_delay_bars=1` run, 28 of 829 trades had an invalid fill-side stop and contributed **+$6,015**. Published timing-stress totals were 829 trades, $161,205 net, PF 1.439, and -$18,100 drawdown.
- **Consequence:** impossible same-bar profits and understated delay risk. The canonical zero-delay 813-trade authority run had zero invalid fill-side stops, so its $160,125 total is not changed by this finding.
- **Smallest remediation:** after calculating the actual fill, reject/cancel the entry when the frozen stop is on the wrong side, or define and document a causal fill-time stop-repricing policy. Do not silently turn a stop into a profit order.
- **Required test:** long and short gap-through-stop cases at normal and delayed next-open fills; assert no position is opened with an invalid stop and no impossible profit is booked.
- **Blocked gate:** promotion-grade simulator claims and all execution-timing conclusions.

### P0-02 - The Tradovate path can submit a second entry while already in a position

- **Severity:** P0 (unintended exposure); high confidence.
- **Files:** `src/full_python/strategy/adaptive_trend.py:109-122`, `173-184`; `src/full_python/simulation/position_engine.py:560-564`, `627-629`; `src/full_python/execution/live_loop.py:94-104`, `142-145`; `src/full_python/tradovate/broker.py:217-255`, `348-374`.
- **Violated invariant:** at most one strategy position or entry attempt may exist, and the strategy must receive authoritative fill/close feedback.
- **Evidence:** the simulator invokes `strategy.on_fill()` and `strategy.on_trade_closed()`, which control the strategy's `_position_side`, exits, cooldown, and anti-martingale state. The Tradovate broker only updates its own state. `LiveLoop._drain_events()` sends fills to the order shadow but never to the strategy. `TradovateBroker.apply_strategy_result()` also does not reject an entry when a position, working entry, pending exit, or recovery state exists.
- **Reproduction:** with fake REST, submit and fill one entry, confirm its protective stop, then pass another entry intent. REST roles were `Market`, `Stop`, `Market`. A second fill is rejected locally only after the real account could already hold excess quantity.
- **Consequence:** duplicate contracts, incorrect exits, stale cooldown/sizing state, and local/account divergence.
- **Smallest remediation:** make broker/account position state the entry gate; persist a legal execution-state machine; dispatch normalized authoritative fills/trade closures to the strategy exactly once; prohibit entries outside `FLAT/NORMAL`.
- **Required test:** protocol-faithful entry-fill-stop cycle followed by repeated strategy signals. Assert exactly one entry submission, strategy position state set on fill, and cleared only after reconciled close.
- **Blocked gate:** demo orders, paper, funded MNQ, unattended production.

### P0-03 - The live broker has no 15:59 ET flatten and shutdown is a no-op

- **Severity:** P0 (uncontrolled overnight exposure); high confidence.
- **Files:** `src/full_python/simulation/position_engine.py:302-320`; `src/full_python/tradovate/broker.py:140-164`, `260-263`.
- **Violated invariant:** the documented 15:59 ET backstop must flatten every open position and cancel entries/exits before the session boundary.
- **Evidence:** the simulator implements `_process_backstop_flatten()`. The Tradovate broker's per-bar hook handles session rollover and DLL only. `close_end_of_data()` explicitly does nothing. The next session merely raises if exposure survived.
- **Reproduction:** an offline broker fixture with an open position processed a 15:59 ET bar and then `close_end_of_data()`. Position remained open and zero liquidation calls were made.
- **Consequence:** an ATF position can remain open overnight, through maintenance, expiration/roll, or process shutdown despite the documented risk boundary.
- **Smallest remediation:** implement a broker-authoritative session-close state: cancel entries, cancel/confirm protection as appropriate, submit flatten, require fill/account-flat confirmation, and halt if confirmation misses a deadline. Shutdown must use the same protocol when exposure exists.
- **Required test:** open position at 15:58, 15:59, early-close minus one minute, SIGTERM, and next-session rollover. Each must end broker-confirmed flat with no working orders, or remain hard halted with an external alert.
- **Blocked gate:** demo orders, paper, funded MNQ, unattended production.

### P0-04 - Liquidation requests do not match the Tradovate API contract

- **Severity:** P0 (emergency control may not execute); high confidence.
- **Files:** `src/full_python/tradovate/broker.py:265-290`, `412-442`.
- **Violated invariant:** every emergency and DLL flatten must use a broker-valid, instrument-specific request and must be confirmed rather than assumed.
- **Evidence:** both paths send `accountSpec`, `accountId`, `symbol`, and `admin`. Tradovate documents `accountId`, **`contractId`**, and `admin` as required for `/order/liquidateposition`; it describes liquidation as a request, not a guarantee. The code neither has the contract ID nor verifies final flat state, and `_emergency_flatten()` silently returns on transport or response failure. See [Tradovate liquidatePosition](https://partner.tradovate.com/api/rest-api-endpoints/orders/liquidate-position).
- **Reproduction:** compare the bodies at the cited lines with the official request schema. Existing fake transports accept arbitrary dictionaries, so current tests cannot detect the mismatch.
- **Consequence:** the control intended for data outage, DLL breach, rejected stop, and late fill may be rejected or target no valid position; loss can continue without a second control path.
- **Smallest remediation:** resolve and persist the exact account/contract identity, send the documented schema, treat the response as acknowledgment only, and require user-event plus REST position/order reconciliation to prove flat/no-working-orders.
- **Required test:** schema-strict fake server rejects `symbol` and requires `contractId`; exercise success, failureReason, timeout-after-acceptance, partial close, stale working order, and final REST mismatch.
- **Blocked gate:** demo orders, paper, funded MNQ, unattended production.

### P0-05 - Unknown POST outcomes can create orphan entries, stops, and exits

- **Severity:** P0 (duplicate or reversing exposure); high confidence.
- **Files:** `src/full_python/tradovate/broker.py:236-255`, `388-410`, `412-454`, `470-508`; `src/full_python/live/persistence.py:21-46`.
- **Violated invariant:** one logical intent must cause at most one broker order, and uncertainty must be reconciled before any retry or state transition.
- **Evidence:** no logical/client order ID is persisted before POST. If the broker accepts and the HTTP response is lost, the order is absent from `_orders`. An unknown accepted protective stop can survive a later liquidation and reverse the account. An unknown exit occurs after confirmed stop cancellation, leaving an open position without known protection. The event ledger cannot reconstruct the missing request/ack boundary.
- **Reproduction:** a fake transport records acceptance and then raises. Entry transitions to recovery without a discoverable identifier; protective-stop failure calls a liquidation whose own outcome may be unknown; exit failure occurs after protection was canceled. No query-by-intent or restart recovery API exists.
- **Consequence:** duplicate entries, orphan protective orders, naked positions, or reversal after apparently successful flattening.
- **Smallest remediation:** durable intent ID before send, idempotent submission policy, account-scoped order/fill synchronization, explicit `SUBMISSION_UNKNOWN`, and reconciliation before retry. Emergency controls need their own confirmed outcome loop.
- **Required test:** crash/timeout at every boundary before send, after broker accept, after ack, after partial fill, and after cancel; restart from durable state and prove one logical order and broker-authoritative position/protection.
- **Blocked gate:** demo orders, paper, funded MNQ, unattended production.

### P1-01 - No production account-event pump, startup hydration, or restart recovery exists

- **Severity:** P1; high confidence.
- **Files:** `src/full_python/live/runner.py:217-288`; `src/full_python/tradovate/broker.py:121-136`, `618-660`; `src/full_python/execution/live_loop.py:142-145`.
- **Violated invariant:** before entries, broker positions, orders, fills, and protection must be hydrated and kept current from broker truth.
- **Evidence:** the only composition root is observe-only and creates no order-capable broker. `ingest_raw_event()` and `reconcile_rest_positions()` have no production caller. Tradovate recommends `user/syncrequest` for real-time user data and heartbeats; see [Tradovate WebSocket best practices](https://partner.tradovate.com/resources/reference/best-practices) and its [official sync example](https://github.com/tradovate/example-api-faq).
- **Reproduction:** source search finds `ingest_raw_event` only in its definition and tests. Constructing a new broker always starts `_orders={}` and `_position=None` regardless of account state.
- **Consequence:** fills never reach live state; restart can mistake an open account for flat and enter again; manual/platform actions remain unknown.
- **Smallest remediation:** build a demo-only account synchronization service with startup snapshots, user-event sequence handling, heartbeat/reconnect, deduplication, periodic REST reconciliation, and an entry latch that opens only after agreement.
- **Required test:** restart with open/flat/working-stop/unknown-order states against a protocol-faithful server; no entry until order, fill, position, and protection snapshots agree.
- **Blocked gate:** demo orders, paper, funded MNQ, unattended production.

### P1-02 - REST reconciliation ignores account and contract identity

- **Severity:** P1; high confidence.
- **Files:** `src/full_python/tradovate/broker.py:618-660`.
- **Violated invariant:** only the configured account and exact active contract may reconcile the local strategy position.
- **Evidence:** all nonzero `netPos` rows are accepted/summed without filtering `accountId` or `contractId`. Tradovate's documented position entity contains both fields; see [Position List](https://partner.tradovate.com/api/rest-api-endpoints/positions/position-list).
- **Reproduction:** a local NQ long one was reconciled successfully against a synthetic row for another account and an ES contract with `netPos=1`. Existing tests use rows without either identity field.
- **Consequence:** false agreement can authorize entries or suppress emergency action while the actual account/instrument differs.
- **Smallest remediation:** resolve configured account ID and tradable contract ID, reject foreign rows, separately surface any unexpected nonzero account position, and reconcile orders as well as net position.
- **Required test:** mixed accounts, mixed contracts, roll straddle, duplicate rows, missing identity, wrong quantity, wrong side, and unexpected foreign exposure.
- **Blocked gate:** demo orders, paper, funded MNQ, unattended production.

### P1-03 - A cold-start market-data outage is never armed

- **Severity:** P1; high confidence.
- **Files:** `src/full_python/livedata/live_bar_source.py:61-76`, `89-101`; `src/full_python/live/runner.py:83-99`, `217-288`.
- **Violated invariant:** starting inside the active entry/risk window without data must halt after the configured grace period.
- **Evidence:** before the first bar, `expected=None`; `_armed(None)` returns false unless a position provider already reports open. Repeated timeouts loop forever. The runner's wall-clock end is checked only after a bar is yielded.
- **Reproduction:** at 09:35 ET with no feed bars and no local position, `_armed(None)` was false while `_armed(clock.now())` was true. No exception or session report is reachable.
- **Consequence:** an operator can believe observation is running while no market data, no evidence, and no end-of-session completion exist.
- **Smallest remediation:** arm cold start from injected clock and active window; enforce a startup deadline and always produce a halt report.
- **Required test:** start before, inside, and after active window; no first bar; late first bar; process until configured end. Inside-window startup must halt deterministically.
- **Blocked gate:** demo observation and every later gate.

### P1-04 - NaN and infinity bars pass offline and live integrity checks

- **Severity:** P1; high confidence.
- **Files:** `src/full_python/data/validation.py:136-148`; `src/full_python/livedata/live_bar_source.py:103-115`.
- **Violated invariant:** OHLCV and derived prices must be finite, positive where required, and internally consistent before strategy state mutates.
- **Evidence:** comparisons with `NaN` are false and `inf` can satisfy the current OHLC ordering. Live normalization performs no finite/OHLC/volume validation.
- **Reproduction:** an offline bar with `high=inf` and `volume=nan` returned `issue_counts={}` and `is_structurally_clean=True`.
- **Consequence:** poisoned indicators, silent signal suppression/creation, invalid P&L, and unusable reports.
- **Smallest remediation:** centralize strict `MarketBar` validation using `math.isfinite`, valid OHLC, nonnegative finite volume, tick alignment where appropriate, and fail closed before mutation.
- **Required test:** NaN and +/-infinity in every field, zero/negative prices, invalid OHLC, malformed timestamp, and live vendor equivalents.
- **Blocked gate:** promotion-grade research and demo observation.

### P1-05 - The claimed live/backtester parity test is circular

- **Severity:** P1; high confidence.
- **Files:** `HANDOFF.md:75-79`; `src/full_python/execution/paper_broker.py:1-8`, `27-52`; `tests/test_live_loop_identity.py:87-113`, `165-187`; `tests/test_tradovate_live_loop.py:20-41`, `56-68`, `103-149`.
- **Violated claim:** the full live path reproduces the backtester trade-for-trade.
- **Evidence:** `PaperBroker` intentionally wraps the same `PositionEngine`; comparing it with `SimulationEngine` proves orchestration identity around a shared implementation, not independent execution correctness. Tradovate tests use scripted fake REST and manually normalized fills. The real Tradovate adapter has different lifecycle code and does not feed fills back to the strategy.
- **Reproduction:** follow class construction in `PaperBroker`: both expected and actual fill/stop/P&L values come from the same engine. Replace it with `TradovateBroker`; the P0-02 duplicate-entry reproduction appears.
- **Consequence:** green tests support a stronger safety claim than they establish.
- **Smallest remediation:** narrow the documentation claim; add a protocol-faithful independent broker model and later broker-demo reconciliation, with expected state derived from account events rather than `PositionEngine`.
- **Required test:** full strategy signal -> REST -> user sync ack/fill -> protective stop -> exit -> fill -> REST reconciliation using actual Tradovate schemas and no shared simulator lifecycle.
- **Blocked gate:** demo orders, paper, funded MNQ.

### P1-06 - The edge has no untouched final holdout; "walk-forward" is descriptive slicing

- **Severity:** P1; high confidence.
- **Files:** `HANDOFF.md:10-19`, `95-111`; `scripts/run_baseline_walk_forward.py:39-95`; `src/full_python/research/walk_forward.py:51-78`, `92-112`.
- **Violated claim:** the strategy is already validated and anchored walk-forward results independently validate it.
- **Evidence:** the script loads trades generated once over the full history and slices them by validation dates. Its train periods never fit, select, or freeze a model. The previous one-shot holdout has been inspected and reused. Numerous Pine and Python iterations touched overlapping history, while the total historical trial count is unknown.
- **Reproduction:** lines 43-85 read completed NQ/MNQ reports and trades; no training operation consumes a train fold. The output is useful chronological attribution, not untouched out-of-sample evidence.
- **Consequence:** apparent performance may include selection bias, repeated-testing bias, and regime-specific tuning.
- **Smallest remediation:** relabel current folds as chronological stability segments; freeze code/config and collect genuinely prospective data, or define a future untouched holdout before any new result is viewed. Preserve all trial history for multiple-testing analysis.
- **Required test/evidence:** immutable preregistration timestamp, code/data/config hashes, no-access holdout policy, prospective results, and a model-selection correction based on the complete trial family.
- **Blocked gate:** claims of validated edge, funded MNQ, unattended production.

### P1-07 - Continuous-contract provenance is incomplete and deduplication can hide conflicts

- **Severity:** P1; medium-high confidence.
- **Files:** `src/full_python/data/databento.py:1-11`, `63-109`, `175-223`; `.gitignore:8-10`.
- **Violated invariant:** every historical bar must map to an exact source batch and tradable contract, with deterministic conflict handling.
- **Evidence:** the builder uses a custom TradingView-fitted roll rule, writes every output symbol as `NQ1!`, returns a roll map only to the caller, and silently keeps the first duplicate timestamp without testing whether OHLCV conflicts. The authoritative CSV hash is reproducible locally, but source batch IDs, mappings, and roll map are not committed with the report. Databento's official continuous symbology maps to actual instruments and provides original unadjusted prices plus mapping metadata; see [Databento symbology](https://databento.com/docs/standards-and-conventions/symbology).
- **Reproduction:** lines 195-200 drop duplicate timestamps unconditionally; lines 209-223 return but do not persist `roll_map` into the canonical CSV. The five-year report identifies `NQ1!`, not each underlying contract.
- **Consequence:** roll-specific signals cannot be traced to an executable contract; conflicting gap-fill batches can be hidden; future rebuilds may not reproduce the same chain.
- **Smallest remediation:** persist vendor job IDs/file hashes, raw-to-output symbology mapping, roll rule/version, per-session contract, and conflict report; fail on unequal duplicate bars.
- **Required test:** overlapping source files with identical and conflicting rows; every canonical row must resolve to one source record and contract ID; rebuild hash must match from archived source manifest.
- **Blocked gate:** final research validation and any contract-specific execution comparison.

### P2-01 - Sharp parameter peaks are labeled as robustness

- **Severity:** P2; high confidence.
- **Files:** `docs/decisions/2026-07-03-1contract-sweep-3yr.md:11-23`, `49-56`; `HANDOFF.md:116-119`.
- **Violated research principle:** robustness should appear as a stable plateau or mechanism, not a uniquely optimal point selected after inspection.
- **Evidence:** nearby max stops reduced net by $4.7K-$21.0K, signal-valid neighbors by $13.6K-$17.2K, and ATF sensitivity neighbors by $12.1K-$16.2K. The decision calls these "sharp" optima and then "strong robustness evidence." A narrow peak is evidence of sensitivity, even if repeated on another overlapping window.
- **Reproduction:** read the single-axis table at lines 13-19 and compute each neighbor's delta from the selected setting; no adjacent plateau exists for the three cited axes.
- **Consequence:** higher risk that observed settings are selected noise and degrade prospectively.
- **Smallest remediation:** retain the frozen candidate but relabel the evidence; use plateau metrics, perturbation distributions, and prospective validation rather than choosing another optimum.
- **Required evidence:** neighboring settings remain economically viable under identical future data, costs, and trial accounting; no retuning on the validation period.
- **Blocked gate:** validated-edge claim; not deterministic replay.

### P2-02 - Bootstrap and experiment registry do not correct model selection

- **Severity:** P2; high confidence.
- **Files:** `src/full_python/reporting/bootstrap.py:93-112`, `135-177`; `src/full_python/research/registry.py:52-163`; `scripts/run_baseline_walk_forward.py:57-95`; `.gitignore:8-10`.
- **Violated claim:** preregistration and bootstrap bands make current conclusions statistically decision-grade.
- **Evidence:** the moving-block bootstrap resamples one selected strategy with one 10-session block length and assumes the historical session process is representative. It does not include strategy selection or trial multiplicity. The SQLite registry enforces a budget inside one local file, but `runs/` is ignored, the database can be replaced, and scripts register immediately before reading/recording existing results.
- **Reproduction:** remove or copy `runs/experiments.sqlite` and rerun a script: a fresh registry accepts the same experiment as new. Change `block_length_sessions` and observe that the reported distribution changes without any model-selection penalty.
- **Consequence:** the 0.35% empirical nonpositive annual-net frequency can be mistaken for the probability that the edge exists. It is only a conditional resampling statistic.
- **Smallest remediation:** preserve a tamper-evident trial ledger outside ignored artifacts; report block-length sensitivity; use White Reality Check/SPA, Deflated Sharpe, or PBO only after reconstructing the relevant trial family.
- **Required test/evidence:** immutable registry lineage, complete candidate count, multiple block lengths, selection-adjusted statistic, and wording that separates empirical resampling from posterior belief.
- **Blocked gate:** statistical validation and capital claims.

### P2-03 - Simulation accepts negative commission and slippage

- **Severity:** P2; high confidence.
- **Files:** `src/full_python/simulation/config.py:21-64`.
- **Violated invariant:** ordinary promotion runs cannot create artificial rebates or favorable slippage through invalid configuration.
- **Evidence:** `SimulationConfig(commission_per_contract_round_trip=-10, entry_slippage_points=-1, exit_slippage_points=-1)` constructs successfully.
- **Reproduction:** execute that constructor in a Python shell; it returns an instance rather than raising, and its parameter hash treats the invalid values as a normal run configuration.
- **Consequence:** a typo can inflate every trade while still receiving a valid parameter hash and report.
- **Smallest remediation:** require nonnegative finite costs/slippage, finite point value, and sensible flatten clock values; isolate any explicit rebate model behind a named policy.
- **Required test:** negative, NaN, and infinity for all numeric execution fields must fail construction.
- **Blocked gate:** promotion-grade research automation.

### P2-04 - Projected DLL sizing is not a hard loss cap

- **Severity:** P2; high confidence.
- **Files:** `src/full_python/strategy/adaptive_trend.py:275-288`; `src/full_python/simulation/position_engine.py:511-526`; `src/full_python/tradovate/broker.py:170-180`.
- **Violated expectation:** projected stop risk must not be described as guaranteeing the daily-loss limit.
- **Evidence:** quantity uses signal-to-stop points only. It excludes entry/exit slippage, commissions, gaps through stop, latency, and unknown fills. The live broker's DLL is bar-close/local-ledger based rather than account-authoritative.
- **Reproduction:** compare `_dll_safe_quantity()` with fill and stop-gap pricing: for any accepted quantity, add entry slippage, stop slippage/commission, or a gap beyond stop; realized loss exceeds the value used at line 282 while the pretrade quantity is unchanged.
- **Consequence:** realized/account loss can exceed the nominal $1,000 strategy DLL or $150 MNQ pilot daily cap.
- **Smallest remediation:** name it a pretrade budget guard; include conservative all-in loss and gap allowance; enforce an independent broker/account supervisor and hard external limits where available.
- **Required test:** gap-through-stop, doubled costs, late fill, partial fill, manual loss, and account-vs-local mismatch must block entries/trigger confirmed risk action.
- **Blocked gate:** funded MNQ risk gate.

### P2-05 - The persistent ledger is flushed, not crash-safe or replayable

- **Severity:** P2; high confidence.
- **Files:** `src/full_python/live/persistence.py:1-10`, `21-46`; `src/full_python/events.py:23-45`, `48-84`.
- **Violated claim:** the live event file loses nothing recorded and supports financial recovery.
- **Evidence:** each JSON line is flushed but never `fsync`ed; a torn final line is not detected/repaired. Records lack run/schema version, logical intent ID, broker/account/contract identity, causal links, and hash chaining. A restart intentionally opens a new file and does not replay state.
- **Reproduction:** inspect `append()` at lines 40-43, then truncate the last JSON line and call `EventLedger.read_jsonl()`; parsing fails rather than recovering a verified prefix. No method can rebuild broker state from the remaining records.
- **Consequence:** power loss can lose acknowledged events; files cannot prove exactly-once execution or restore state.
- **Smallest remediation:** narrow the claim to best-effort trace, then design a durable state journal with fsync policy, checksums/versioning, causal IDs, tail recovery, and broker reconciliation.
- **Required test:** kill/power-loss injection at every append and order boundary; recover the last valid record and reconcile to the same broker state.
- **Blocked gate:** paper, funded MNQ, unattended production.

### P2-06 - Token renewal, reconnects, telemetry, and dependency reproducibility are incomplete

- **Severity:** P2; high confidence.
- **Files:** `src/full_python/live/runner.py:252-288`; `pyproject.toml:1-21`; no `.github/workflows/` or dependency lock file.
- **Violated operational principle:** long-running sessions must retain authenticated connectivity and produce actionable health evidence from reproducible builds.
- **Evidence:** renewal replaces only `token_state`; the already-authenticated HTTP client is not rebuilt and the market-data WebSocket has no reconnect/re-subscribe path. Observe mode presently makes no later REST calls, so this is dormant there but unsafe for future order mode. Dependencies have lower bounds only, no lock, no CI, and no supported environment matrix. There is no pager/alert sink for stale feed, missing protection, reconciliation drift, or liquidation failure. Tradovate tokens expire after 90 minutes and should be renewed about 15 minutes early; see [Access Token Request](https://partner.tradovate.com/api/rest-api-endpoints/authentication/access-token-request).
- **Reproduction:** force `should_renew()` true and trace object references: `token_state` changes, while `authed_http` and the authorized `ws_client` remain unchanged. Source inventory confirms no reconnect loop, lock file, or CI workflow.
- **Consequence:** silent session death, stale REST authorization, irreproducible environments, and unattended failures with no operator response.
- **Smallest remediation:** reconnect/re-authorize state machine, subscription replay, sequence-gap handling, refreshed client injection, structured health metrics/alerts, pinned dependency lock, and CI.
- **Required test:** token expiry, WS close, heartbeat loss, rate limit, maintenance, DNS failure, process restart, and dependency-clean install.
- **Blocked gate:** dependable demo observation and unattended production.

### P3-01 - Documentation overstates maturity and contains stale checkpoints

- **Severity:** P3; high confidence.
- **Files:** `HANDOFF.md:10-19`, `75-79`, `83-114`, `153-156`.
- **Violated principle:** the handoff must distinguish historical arithmetic, research evidence, simulator identity, and broker execution proof.
- **Evidence:** "ALREADY VALIDATED," "all pass and prove the live path," and "Phase 2 robust experimentation COMPLETE" exceed the evidence above. The checkpoint says `4de6b98`; audited main is `dce7988`.
- **Reproduction:** compare `git rev-parse HEAD` and `git rev-parse origin/main` with lines 153-156, then trace the parity test construction at `PaperBroker`; both checks contradict the current shorthand.
- **Consequence:** a future operator or agent can advance gates based on shorthand rather than evidence.
- **Smallest remediation:** replace maturity claims with layer-specific status and link this audit; regenerate commit/test counts mechanically.
- **Required test:** documentation check that embeds current commit, test count, capability classification, and unresolved blockers from machine-readable status.
- **Blocked gate:** none by itself; it amplifies every promotion risk.

## Audit Coverage and Method

The audit inspected all tracked production Python modules under `src/full_python/`, all 65 test modules, all 25 current decision records, design/runbook/configuration files, ignored-artifact rules, dependency declarations, and relevant git history through the audited commit. It searched for credential material, order-capable composition roots, event-ingestion callers, reconciliation callers, and CI/dependency-lock configuration. No committed credential was found. No `.github/workflows/` directory or dependency lock file was present.

Executable work included both full test-suite modes, an independent five-year replay, raw-input hashing, one long and one short causal trace, 11 missed-signal seeds, a delayed-entry audit, and focused offline broker/data counterexamples. Official Tradovate, CME, and Databento documentation was used only for external protocol, instrument, and symbology facts. All temporary reproductions stayed outside the repository; only this audit document was added.

## 2. Executive Verdict by Layer

| Layer | Verdict | Evidence-based boundary |
|---|---|---|
| Research | **RESEARCH-ONLY** | Five-year historical arithmetic is reproducible and the strategy thesis is coherent, but there is no untouched final holdout or selection-adjusted proof of persistence. |
| Simulator | **Conditionally usable for research, not promotion-valid** | Canonical baseline is deterministic, but delayed fills can create impossible profitable stops and numeric config/data validation is incomplete. |
| Demo observation | **Not ready** | Order-disabled construction is good, but cold-start outage handling fails and three clean independent demo sessions are not present. |
| Demo orders | **Reject** | Duplicate entry, liquidation schema, no session flatten, no user-event pump, and no unknown-outcome recovery. |
| Paper trading | **Reject** | Current `PaperBroker` is simulator identity, not broker-protocol paper evidence; restart/reconciliation is absent. |
| Limited funded MNQ | **Reject** | Research sizing cannot override unresolved execution and account-risk controls. |
| Unattended production | **Reject** | No durable recovery, account authority, confirmed emergency control, observability, or operational history. |

The strongest defensible statement is: **the repository can deterministically replay one historically profitable strategy under an explicit one-minute bar model. It has not yet proven a persistent edge or a safe broker execution system.**

## 3. Verified Numerical Reproduction

The full offline suite was run twice:

```text
python3 -m pytest -q
397 passed, 4 skipped in 13.97s

FULL_PYTHON_BASELINE_DATA=runs/baseline-anchor/nq1_2025-10-01_2026-06-26.csv python3 -m pytest -q
401 passed in 78.37s
```

The five-year authority run was independently replayed from `runs/multi-year/nq1_2021-03-16_2026-06-26.csv` with NQ $20/point, $10 round-trip commission, and 0.75 points slippage per side:

| Metric | Reproduced result |
|---|---:|
| Bars | 1,871,670 |
| Data range | 2021-03-16 through 2026-06-26 |
| Trades | 813 |
| Net P&L | $160,125 |
| Profit factor | 1.419862 |
| Win rate | 22.140% |
| Expectancy/trade | $196.96 |
| Max drawdown | -$18,570 |
| Max loss streak | 22 |
| Long net | $95,175 |
| Short net | $64,950 |
| Net without top 5 trades | $102,785 |
| Net without top 10 trades | $62,305 |
| Path-ambiguous exits | 59 |

Year attribution: 2021 $15,965; 2022 $26,755; 2023 -$8,400; 2024 $44,430; 2025 $30,575; 2026 through June $50,800. Only 35 of 64 months were positive.

The canonical input SHA-256 reproduced as `51ec82985be1775f3519967db8bd0a0afa65ee0cdffe438a9e4910f8772e0f81`. The source-tree identity was clean at the audited commit.

The deterministic 10-session moving-block bootstrap also reproduced: annualized net 95% interval approximately $8,466-$51,147; Sharpe interval 0.407-1.958; max-drawdown p95 approximately -$42,545 and p99 approximately -$54,603; empirical nonpositive annual net 0.35%. These are conditional resampling outputs, not a probability that the edge is real.

### End-to-end long trace

At 2021-03-16 13:45 UTC, close 13,200.75 exceeded confirmed pivot resistance 13,185.50. MA50 was 13,160.4218, MA200 13,141.9463, ATF was long, squeeze momentum was +20.4754 and green/released, wings and cooldown passed, and two-bar prove-it passed. The order filled next bar at 13,202.00 after slippage with stop 13,180.50. ATF flip signaled at 16:14 UTC; next-open exit was 13,218.75 after slippage. Gross +16.75 points = $335; net after $10 commission = **$325**.

### End-to-end short trace

At 2021-03-19 13:42 UTC, close 12,733.50 broke confirmed pivot support 12,765.50. MA50 was 12,803.8139, MA200 12,838.5388, ATF was short, squeeze momentum was -17.0372 and red/released, and wings/cooldown/prove-it passed. Next-open fill was 12,733.75 after slippage with capped stop 12,764.50. The stop filled at 12,765.25 after slippage. Gross -31.50 points = -$630; net = **-$640**.

These traces found no lookahead in pivot confirmation or next-open fill ordering. They prove implementation behavior for these trades, not general profitability.

### Multi-seed missed-signal stress

The published 90% fill stress used one seed. This audit ran seeds 0-9 plus 20260713 over all five years. Every seed remained net positive, but outcomes varied:

| Range across 11 seeds | Result |
|---|---:|
| Net P&L | $108,995 to $153,250 |
| Profit factor | 1.305 to 1.446 |
| Max drawdown | -$12,960 to -$28,265 |
| Positive six-month segments | 3 of 7 to 6 of 7 |
| Net without top 10 trades | $16,840 to $59,500 |

This is useful robustness evidence for random deterministic signal deletion. It does not model whether marketable NQ orders fill, and it does not cure the absence of prospective validation.

## 4. Claim Ledger

| Claim | Status | Audit conclusion |
|---|---|---|
| Nothing currently trades live; observe CLI cannot enable orders | **Verified** | Construction and CLI enforce orders disabled; no credentials are committed. |
| Five-year Python baseline is 813 trades / $160,125 / PF 1.420 | **Verified** | Independently reproduced from local canonical CSV and clean source. |
| NQ/MNQ are $20/$2 per point | **Verified** | Code remediation matches [CME NQ/MNQ specifications](https://www.cmegroup.com/markets/equities/nasdaq/nasdaq-futures.html). |
| Baseline support/resistance breakout logic is causal | **Verified for inspected implementation** | Confirmed pivots and prove-it use prior/confirmed bars; long and short traces agree. |
| Dirty source state enters run identity | **Verified** | Phase 0 remediation is present and tests pass. |
| RTH gaps fail closed | **Verified for offline validator tests** | Current five-year report has no RTH gaps; non-RTH gaps remain informational. |
| Stop-bar MFE/MAE is bounded and flagged | **Verified** | Current stop-first logic preserves confirmed MFE and marks ambiguous favorable extremes. |
| Strategy is "ALREADY VALIDATED" | **Unsupported** | Historical profitability is real under the model; persistence and selection independence are not established. |
| Phase 0 correctness remediation is complete | **Contradicted** | Delayed-entry stop inversion and live-path blockers remain. |
| Full live path reproduces backtester trade-for-trade | **Contradicted** | Paper identity shares `PositionEngine`; Tradovate lifecycle is separate and fails an entry-state repro. |
| TradingView baseline matches 106/106 trades | **Partly verified** | Entry overlap is documented at zero entry-price delta; Python has extra trades outside TV history and 13 exit-time differences, so full trade parity is not exact. |
| Anchored walk-forward validates out-of-sample behavior | **Partly verified** | Fold arithmetic is real; it is chronological slicing, not model refitting or untouched OOS. |
| NQ positive in 5/7 and MNQ 4/7 six-month folds | **Verified as arithmetic** | Both halves of 2023 lose; this does not prove a stationary edge. |
| One-minute latency survives | **Partly contradicted** | Aggregate remains positive, but 28 trades have invalid fill-side stops and add $6,015 of impossible P&L. |
| Ten percent missed signals survives | **Verified conditionally** | All 11 audited seeds remain positive, with wide path dispersion and as few as 3/7 positive segments. |
| Tradovate adapter is offline-complete | **Partly verified** | Useful adapter skeleton and safety defaults exist; broker protocol/recovery is not complete enough for orders. |
| Demo observe runner is built | **Verified** | It is order-impossible by construction; cold-start handling and real-session evidence are incomplete. |
| Three clean demo observe sessions exist | **Unsupported** | No committed or otherwise supplied session artifacts prove the gate. |
| MNQ pilot loss probabilities authorize a funded pilot | **Unsupported as permission** | They are conditional planning estimates only; all operational gates remain blocked. |

## 5. Adversarial Failure Matrix

| # | Incident | Required behavior | Current behavior / evidence | Covered adequately? |
|---:|---|---|---|---|
| 1 | Next-open gaps beyond planned long stop | reject/reprice under explicit policy | opens with stop above entry and can book profit | **No; reproduced failure** |
| 2 | Next-open gaps beyond planned short stop | reject/reprice under explicit policy | opens with stop below entry and can book profit | **No; reproduced failure** |
| 3 | Repeated signal while entry working | submit once | no broker entry-state veto | **No** |
| 4 | Repeated signal after entry fill | remain one contract | submits second market entry | **No; reproduced failure** |
| 5 | Strategy fill feedback | strategy mirrors broker state exactly once | Tradovate fill never reaches strategy | **No** |
| 6 | 15:59 ET with open position | broker-confirmed flat | no backstop in Tradovate broker | **No; reproduced failure** |
| 7 | SIGTERM/shutdown with exposure | defined confirmed-flat or takeover protocol | `close_end_of_data()` no-op | **No** |
| 8 | Early-close session | flatten relative to exchange close | simulator calendar exists; broker has no flatten | **No end-to-end test** |
| 9 | Liquidation request | use contractId and confirm flat | sends symbol/accountSpec; response assumed | **No; schema mismatch** |
| 10 | Entry accepted, HTTP response lost | discover same order; never retry blindly | unknown orphan order, no client intent ID | **No** |
| 11 | Stop accepted, HTTP response lost | discover/cancel exact stop before liquidation | unknown stop can later reverse | **No** |
| 12 | Exit accepted, HTTP response lost | reconcile before retry | protection already canceled; unknown close | **No** |
| 13 | Partial entry fill | protect filled quantity and reconcile remainder | quantity 1 temporarily avoids but does not model it | **No** |
| 14 | Partial exit/liquidation | maintain residual position/protection | exact-quantity assumption raises | **No** |
| 15 | Stop fills during cancel | do not send second close | cancel-confirm remediation handles known event ordering | **Partly** |
| 16 | Unsolicited protective-stop cancel | re-protect/flatten and halt | remediation requests emergency action, but liquidation is invalid/unconfirmed | **Partly, not safe** |
| 17 | Exit rejection | restore protection or confirmed flatten | emergency action attempted, unconfirmed | **Partly, not safe** |
| 18 | Broker reports manual/unknown fill | incorporate account truth and halt entries | no production user-event pump/recovery | **No** |
| 19 | Restart with open position | hydrate account, orders, stop, P&L | initializes flat/empty | **No** |
| 20 | REST snapshot for wrong account | reject and alert | accepted if side/qty match | **No; reproduced failure** |
| 21 | REST snapshot for wrong contract | reject and alert | accepted if side/qty match | **No; reproduced failure** |
| 22 | User WebSocket disconnect/sequence gap | reconnect, resync, reconcile | no account WebSocket exists | **No** |
| 23 | Market-data cold start outage | halt after grace inside active window | loops forever unarmed | **No; reproduced failure** |
| 24 | Mid-session market-data outage | flatten if broker says open, halt, report | designed, but liquidation body/outcome unsafe | **Partly** |
| 25 | Duplicate/out-of-order bar | reject before state mutation | monotonic check handles after first bar | **Yes for simple cases** |
| 26 | NaN/Inf OHLCV | fail closed | can pass as structurally clean | **No; reproduced failure** |
| 27 | Missing RTH minute | invalidate research/live active state | offline fails closed; live catches after first bar | **Mostly** |
| 28 | Conflicting duplicate Databento rows | fail and name source conflict | first row silently wins | **No** |
| 29 | Roll-date mismatch | retain exact contract mapping and compare | custom TV rule; output loses per-row contract | **No full lineage** |
| 30 | Token expiry | renew all clients or reconnect | token object changes; clients remain old | **No long-session integration** |
| 31 | Tradovate heartbeat/rate limit | backoff, alert, preserve state | no order/user stream implementation | **No** |
| 32 | Ledger torn tail/power loss | recover valid prefix and reconcile | flush only; no fsync/tail recovery | **No** |
| 33 | Account P&L differs from local | broker/account value blocks entries | local paired trades and mark only | **No** |
| 34 | DLL stop gap exceeds budget | account supervisor contains loss | projected guard omits gap/costs | **No hard guarantee** |
| 35 | Holiday schedule changes after release | update/version schedule and fail safely | static calendar must be maintained manually | **Partial** |
| 36 | Dependency update changes numerics | locked environment and CI comparison | lower bounds only; no lock/CI | **No** |

## 6. Missing-Test Inventory

### P0 tests

1. Fill-time stop-side validation for long/short gaps, delay, and slippage.
2. Broker legal-state tests that prohibit entry while entry-working, open, exit-pending, halted, or recovery-required.
3. Strategy feedback exactly once from Tradovate entry and exit fills.
4. Protocol-strict `liquidateposition` request with `contractId` and final flat/no-orders confirmation.
5. Timeout-after-acceptance and crash recovery for entry, stop, cancel, exit, and liquidation.
6. 15:59/early-close/shutdown confirmed flatten using broker account truth.

### P1 tests

7. Realistic `user/syncrequest` event stream with duplicate, reordered, missing, partial, reject, cancel, and late-fill events.
8. Startup hydration/restart matrix for flat, open, partially filled, working stop, and unknown order.
9. Account/contract-scoped REST position and order reconciliation.
10. Cold-start data outage and always-written halt report.
11. NaN/Inf/malformed bar validation shared by offline and live paths.
12. Independent end-to-end broker model that does not reuse `PositionEngine`.
13. Exact Tradovate request/response fixtures captured from demo documentation or sanctioned demo sessions, with secrets removed.

### P2 tests

14. Conflicting duplicate source rows and complete roll/source lineage.
15. Negative/nonfinite simulation configuration rejection.
16. Token renewal, reconnect, resubscribe, heartbeat, rate-limit, and maintenance-window tests.
17. Power-loss/torn-ledger recovery and state replay.
18. Bootstrap block-length sensitivity and complete trial-ledger integrity.
19. Clean environment install under locked dependencies and CI on supported Python versions.

## 7. Quantitative-Validity Assessment

### What is credible

- The implementation has a coherent NQ opening momentum/S/R breakout thesis.
- Both long and short sides contribute historically: $95,175 and $64,950.
- The result is not dependent on one or five trades: removing the top five leaves $102,785; removing the top ten leaves $62,305.
- Doubled costs and all 11 deterministic 90%-signal seeds remain net positive in the existing history.
- The 2023 loss and 29 negative months are visible rather than hidden.

### What is not established

- No untouched final holdout remains.
- The complete Pine-plus-Python search count is unavailable, so selection-adjusted significance cannot be computed honestly.
- Fold reporting is descriptive, not an independently refit walk-forward process.
- Parameters sit at sharp local peaks rather than broad plateaus.
- The fixed one-minute bar model does not establish queue position, spread, latency, stop gaps, or exact intrabar excursion sequence.
- The custom continuous roll and NQ-quality signal data do not prove executable MNQ fills and costs.
- A $5,000 or $10,000 monthly target is not a scientific success criterion. The observed five-year NQ mean is not a reliable monthly promise, and MNQ scales dollar outcomes by roughly one tenth before differing costs/slippage.

### Edge classification

The apparent edge is **historically plausible and worth preserving for prospective testing**, not proven. Right-tail dependency is material but not fatal. Regime dependence is material: 2023 loses and the strongest profit appears in later halves. Parameter sensitivity and repeated inspection are the larger threats than top-trade concentration.

## 8. Phased Remediation Plan

### Phase 0 - Restore correctness boundaries

1. Fix fill-time stop validation and rerun every timing axis.
2. Add strict finite bar and simulation-config validation.
3. Correct the handoff claims and classify existing fold/bootstrap outputs accurately.
4. Preserve one clean canonical baseline rerun after fixes.

**Exit criteria:** all P0-01/P1-04/P2-03 tests pass; baseline differences are explained trade-by-trade; no invalid stop exists under any latency setting.

### Phase 1 - Make demo observation trustworthy

1. Fix cold-start outage arming and guaranteed halt reports.
2. Add reconnect, heartbeat, token renewal, reference-bar comparison, and telemetry.
3. Pin dependencies and run CI.
4. Complete three nonconsecutive demo observation sessions, including one restart/outage drill.

**Exit criteria:** zero unexplained bar/signal differences, no stale interval, successful recovery drill, complete redacted artifacts, and no order-capable code path in the observer.

### Phase 2 - Build broker-authoritative execution offline

1. Define one legal execution state graph and one state owner.
2. Add durable logical intents/idempotency and a user-event synchronization service.
3. Hydrate/reconcile exact account, contract, orders, fills, protection, and P&L at startup and periodically.
4. Correct liquidation protocol and implement confirmed session close/shutdown.
5. Feed authoritative fills/trades back to strategy exactly once.

**Exit criteria:** every P0/P1 broker failure-matrix test passes against a schema-strict simulator; crash injection at every boundary recovers without duplicate exposure.

### Phase 3 - Tradovate demo orders

Use a dedicated demo account only. Run one-contract scripted scenarios before any strategy-driven session: entry/protection/cancel/exit, stop fill, reject, disconnect, timeout, restart, duplicate event, partial fill, DLL, and session close.

**Exit criteria:** account snapshots, user events, local state, event ledger, and intended state reconcile exactly; every position has confirmed protection; every emergency drill reaches confirmed flat; no manual database edits.

### Phase 4 - Prospective paper evidence

Freeze strategy and execution configuration. Collect at least 30 sessions without retuning, report all signals/rejections, and compare modeled versus demo fills and operational incidents.

**Exit criteria:** predefined parity/slippage/incident thresholds pass; no P0/P1 event; prospective performance remains within adverse planning bands; operator completes restart and kill-switch drills.

### Phase 5 - Limited MNQ consideration

Recompute account rules and risk from current broker/prop documentation. If every earlier gate passes, consider only the already documented flat-one-MNQ, at-most-10-session operational pilot. This is not permission in this audit.

**Exit criteria:** explicit operator approval, independent risk review, hard external limits, capital/loss budget acceptance, daily reconciliation, and immediate stop on any invariant breach.

### Phase 6 - Unattended production

Only after sustained supervised operation, redundant monitoring, disaster recovery, credential rotation, dependency/security updates, and a proven operator response process.

**Exit criteria:** months of incident-free supervised evidence, tested failover/recovery, independent audit sign-off, and no unresolved P0/P1/P2 operational finding.

## 9. Exact Promotion Evidence

| Promotion | Minimum evidence required |
|---|---|
| Research -> demo observation | Corrected simulator; clean data/config validation; frozen hashes; independent reference bars; observe runbook; no orders possible. |
| Demo observation -> demo orders | Three clean sessions; cold-start/outage/reconnect drill; exact bar/signal parity; full redacted artifacts; all P0/P1 offline broker tests green. |
| Demo orders -> paper | Schema-valid requests; user-sync event pump; startup/restart reconciliation; confirmed protective stop and flatten; timeout/partial/reject drills; no unresolved discrepancy. |
| Paper -> limited MNQ | At least 30 frozen sessions; modeled-vs-actual fill report; no P0/P1 incidents; account-rule review; risk budget and kill switches verified independently. |
| Limited MNQ -> unattended | Successful limited pilot within predeclared limits; independent reconciliation; operational alerts and on-call response; disaster recovery; fresh security/execution audit. |

## 10. Do Not Proceed

- Do not enable `order_enabled` or `flatten_enabled` against any broker.
- Do not use the current liquidation implementation as a kill switch.
- Do not run demo strategy orders before account-event synchronization and startup reconciliation exist.
- Do not call shared-`PositionEngine` paper identity live parity.
- Do not use the delayed-entry result until invalid stops are fixed and rerun.
- Do not describe bootstrap frequencies as the probability the edge is real.
- Do not retune against the already inspected five-year history and call the result out-of-sample.
- Do not fund an MNQ pilot from historical P&L or sizing simulations alone.
- Do not permit unattended operation without confirmed broker flat/protection and external alerting.

## 11. Open Questions

1. Can the original Databento batch files, job metadata, symbology files, and all custom roll overrides be restored and archived by hash?
2. What exact Tradovate account/contract lookup and `user/syncrequest` schemas are observed in the operator's demo account?
3. Does the broker or prop provider enforce an account-wide DLL, and does it flatten or merely reject new orders?
4. What are the complete current commissions, fees, margin, trading-hour, maintenance, and automation rules for that account?
5. How many Pine and Python strategy/filter/parameter trials were inspected before the frozen configuration?
6. What future date range will be declared untouched, and who controls access until the decision point?
7. What policy should apply when next-open price has already crossed the planned stop: reject, recompute, or market-enter with a newly bounded risk amount?
8. Who receives alerts and has authority to intervene during demo, paper, pilot, and eventual unattended operation?
9. What maximum data staleness, broker-state uncertainty, and flatten-confirmation timeout are acceptable?
10. Are NQ-derived signals intended to execute MNQ contracts directly, and how will contract-roll and fill-basis differences be measured?

## 12. Final Classification

# RESEARCH-ONLY

Deterministic replay and historical reporting are useful and substantially improved. The historical baseline is real under the implemented model, but "validated profitable strategy" is too strong, demo observation has a cold-start safety defect, and the dormant order path contains multiple P0 exposure failures. No test result, backtest, bootstrap, or sizing estimate in this repository currently authorizes demo orders, paper execution, funded MNQ, or unattended trading.
