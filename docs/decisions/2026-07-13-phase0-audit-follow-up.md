# Phase 0 Principal-Audit Follow-up

**Decision:** fix the historical false-performance defect and strict input
validation immediately. Keep the strategy configuration frozen. The system
remains **RESEARCH-ONLY** because the separate broker execution P0/P1 findings
are not addressed by this change.

Authority audit:
`docs/audits/2026-07-13-principal-adversarial-red-team-audit.md`.

## Fill-Time Stop Policy

A signal can be accepted relative to its signal close but become invalid by a
later fill. The simulator previously opened a long below its frozen sell stop,
or a short above its frozen buy stop, and then treated that stop as a profitable
same-bar exit.

The policy is now fail-closed:

- calculate the modeled fill including entry slippage;
- require long stop `< fill` and short stop `> fill`;
- otherwise emit `entry_invalidated_at_fill` with the raw price, modeled fill,
  stop, side, and original intent timestamp;
- do not emit a fill, open a position, or call the strategy fill hook.

This policy models delayed signal delivery as a fresh execution decision at the
later bar open. It does not claim that a previously accepted live market order
can be canceled after it fills; the live broker path needs its own pretrade and
post-fill recovery design.

## Corrected Five-Year Results

Data: NQ one-minute canonical history, 2021-03-16 through 2026-06-26. Costs:
$10 round trip and 0.75 points slippage per side.

| Scenario | Trades | Missed | Invalidated | Net | PF | Max DD | Net without top 10 | Positive chronological segments |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Reference | 813 | 0 | 0 | $160,125 | 1.420 | -$18,570 | $62,305 | 5/7 |
| One-minute latency | 804 | 0 | 28 | $159,935 | 1.437 | -$19,735 | $63,265 | 4/7 |
| 10% missed, seed 20260713 | 743 | 78 | 0 | $153,200 | 1.439 | -$12,960 | $59,500 | 5/7 |
| Latency + 10% missed | 733 | 80 | 26 | $142,520 | 1.424 | -$16,090 | $50,905 | 5/7 |

The reference result is unchanged. The old delayed rows (829 trades / $161,205
and 756 / $143,595) are retired. Trade-count differences are not one-for-one
with invalidations because rejected fills change position, cooldown, and later
signal state.

The operational conclusion remains narrow: historical expectancy survives this
specific latency/missed-signal stress. These scenarios are not queue-position,
spread, or market-order fill models and are not independent out-of-sample proof.
The standard runner now registers this as
`phase2-nq-execution-timing-axis-v2-corrected` and records both missed and
fill-time-invalidated entries.

## Strict Input Validation

- Offline and live OHLCV paths now share finite-value validation.
- NaN/infinite prices or volume fail closed before strategy state mutates.
- Simulation point value, commission, slippage, fill rate, and DLL reject
  nonfinite values; costs and slippage also reject negative values.

## Acceptance Evidence

- Focused regression: 51 tests passed.
- Full offline suite: 413 passed, 4 operator-data tests skipped.
- Full suite with `FULL_PYTHON_BASELINE_DATA`: 417 passed.
- Canonical five-year replay: 813 trades, $160,125, PF 1.419862,
  -$18,570 drawdown.
- Delayed replay: zero completed trades with a nonprotective frozen stop.

## Remaining Blockers

This change does not fix duplicate live entries, missing strategy fill feedback,
the absent broker 15:59 flatten, the invalid liquidation request schema,
idempotency, account-event synchronization, restart hydration, or
account/contract-scoped reconciliation. Demo orders, paper, funded MNQ, and
unattended production remain prohibited.
