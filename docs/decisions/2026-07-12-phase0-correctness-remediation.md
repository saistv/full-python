# Phase 0 Correctness Remediation

**Decision:** Phase 0 from the 2026-07-12 independent audit is complete.
This earns a return to research and independently verified observe sessions. It
does **not** authorize an order-enabled runner, paper orders, or funded trading.

## Scope

The phase closed the audit's immediate correctness set: progressive Tradovate
bar snapshots, independent captured-bar verification, cancel-confirmed exits,
reject/cancel recovery, flat-state order cleanup, placement-error mapping, and
a shared holiday/early-close calendar. The implementation also fixed three
adjacent research blockers found by the adversarial audit: NQ/MNQ point-value
authority, dirty-source provenance, and path-ambiguous MFE/MAE.

## Failure Matrix V2

| ID | Fault | Required result | Evidence |
|---|---|---|---|
| J1 | Repeated same-timestamp chart snapshots | Replace forming bar; emit only after next timestamp | `test_tradovate_feed.py` |
| J2 | Captured bars differ from independent CSV | `DIVERGENCE`; no parity credit | `test_live_session_report.py` |
| J3 | Stop fills after cancel accepted but before cancel confirmed | No market close is submitted | `test_stop_fill_wins_cancel_race_and_suppresses_market_exit` |
| J4 | Exit rejected after stop cancellation | Emergency liquidation request and `RECOVERY_REQUIRED` | `test_exit_rejection_after_confirmed_stop_cancel_emergency_flattens_and_halts` |
| J5 | Protective stop canceled without a local request | Emergency liquidation request and halt | `test_unsolicited_protective_stop_cancel_flattens_and_halts` |
| J6 | Flatten while flat with working entry | Cancel entry; late fill triggers emergency recovery | `test_flatten_while_flat_cancels_working_entry_and_late_fill_recovers` |
| J7 | Placement returns `failureReason` or transport error | Role-specific rejection or halting state error, never `KeyError` | `test_entry_failure_response_is_rejected_without_key_error`; transport test |
| J8 | Holiday or scheduled half-day | No holiday RTH; shared 12:59 ET early-close backstop | `test_sessions.py`; `test_position_engine_flatten.py` |
| J9 | NQ/MNQ dollar-value disagreement | Constructor/run refuses or aligns from one instrument spec | `test_instruments.py`; sizing real-data test |
| J10 | Dirty source or missing RTH minute | New source hash; RTH gap rejects default research run | `test_cli_trades.py`; `test_data_validation.py` |

Multi-contract order submission is deliberately rejected until partial-fill
position/protection semantics exist. A partial-fill event is a reconciliation
halt. This is a pilot safety boundary, not a claim that partial fills cannot
happen.

## Broker State Transitions

| Current | Event | Next | Action |
|---|---|---|---|
| `NORMAL` | Strategy exit with live stop | `EXIT_PENDING_CANCEL` | Request stop cancel; submit no close |
| `EXIT_PENDING_CANCEL` | Stop canceled | `EXIT_PENDING_FILL` | Submit one market close |
| `EXIT_PENDING_CANCEL` | Stop fills | `NORMAL` | Book flat; discard pending close |
| `EXIT_PENDING_FILL` | Exit fills | `NORMAL` | Book flat |
| Any open state | Exit reject or unsolicited stop cancel | `RECOVERY_REQUIRED` | Emergency liquidation request and halt |
| Any | Unknown submission outcome/state contradiction | `RECOVERY_REQUIRED` | Halt for broker reconciliation |

## Corrected Evidence

All runs use the same adverse cost model as the old anchor: 0.75 points entry
and exit slippage, no extra opening slippage, and round-trip commission of $10
for NQ or $1 for MNQ.

| Run | Trades | Net P&L | Max drawdown | Path-ambiguous exits |
|---|---:|---:|---:|---:|
| Old 9-month NQ anchor | 115 | $55,875.00 | -$9,150.00 | 0 (not measured) |
| Corrected 9-month NQ | 112 | $56,805.00 | -$8,965.00 | 15 |
| Corrected 9-month MNQ | 126 | $9,153.00 | -$1,294.00 | 16 |
| Old 5-year NQ | 829 | $159,160.00 | -$19,775.00 | 0 (not measured) |
| Corrected 5-year NQ | 813 | $160,125.00 | -$18,570.00 | 59 |
| Old 5-year MNQ (invalid point split) | 875 | $15,011.50 | -$2,274.50 | 0 (not measured) |
| Corrected 5-year MNQ | 859 | $25,931.50 | -$2,865.50 | 61 |

The corrected calendar removed 16 five-year NQ trades, including three in the
nine-month anchor: Thanksgiving 2025, Martin Luther King Jr. Day 2026, and
Memorial Day 2026. The nine-month removed trades totaled -$930, so corrected
net increased by that amount. P&L for shared trades did not change; excursion
values did, because lows beyond a filled stop are no longer counted as MAE and
favorable stop-bar extremes are now treated as unconfirmed bounds.

The old five-year MNQ sizing conclusion is retired. Its strategy evaluated
projected risk at $20/point while simulation P&L used $2/point. Correcting this
changed permissions and quantities, increasing five-year MNQ net by $10,920
and changing drawdown. Capital-sizing policy must be re-derived in Phase 1.

Generated, gitignored evidence:

- `runs/phase0-corrected-anchor/`
- `runs/phase0-corrected-mnq/`
- `runs/phase0-corrected-nq-5yr/`
- `runs/phase0-corrected-mnq-5yr/`

The committed `tests/fixtures/golden_trades.json` now represents the corrected
112-trade anchor.

## Verification

- Ordinary suite: **371 passed, 4 skipped**.
- Operator-data suite: **375 passed** using
  `runs/baseline-anchor/nq1_2025-10-01_2026-06-26.csv`.
- `git diff --check`: clean.

## Remaining Hard Blocks

The order-enabled path remains prohibited. Phase 0 does not provide persistent
client order IDs, crash/restart reconstruction, an account-event pump,
account-wide P&L authority, durable broker-event ledger linkage, or modeled
multi-contract partial fills. These belong to the paper-integration/recovery
phase and must pass protocol-faithful demo fault injection before any pilot.
