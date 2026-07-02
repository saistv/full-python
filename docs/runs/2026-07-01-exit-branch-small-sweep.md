# Exit Branch Small Sweep

Date: 2026-07-01

Input bar stream:

```text
/private/tmp/full_python_selected_stream_20260701/selected_bars.csv
```

Command:

```bash
PYTHONPATH=src python3 -m full_python.cli sweep-exit-branch \
  --data /private/tmp/full_python_selected_stream_20260701/selected_bars.csv \
  --output-dir /private/tmp/full_python_exit_branch_sweep_small_20260701 \
  --stream-input \
  --session rth \
  --point-value 2 \
  --slippage-points-per-side 1 \
  --commission-per-contract 1 \
  --mfe-activations 30,40 \
  --mfe-givebacks 20,30 \
  --fresh-breakout-clearances 0 \
  --cooldowns 0
```

Outputs:

```text
/private/tmp/full_python_exit_branch_sweep_small_20260701/sweep_results.json
/private/tmp/full_python_exit_branch_sweep_small_20260701/sweep_results.csv
```

## Scope

This was a smoke-sized sweep, not a full optimization. It tested four combinations around the promising MFE trailing plus fresh-breakout branch:

- MFE activation: 30, 40
- MFE giveback: 20, 30
- Fresh-breakout clearance: 0
- Cooldown: 0
- Point value: $2 per point, MNQ-equivalent

## Ranked Results

| Rank | Activation | Giveback | Trades | Win Rate | Net P&L | Max DD | Loss Streak | P&L Without Best 5 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 30 | 20 | 517 | 53.19% | $3,165.00 | -$856.00 | 6 | $1,274.50 |
| 2 | 40 | 20 | 495 | 45.05% | $2,958.00 | -$1,621.00 | 10 | $1,061.50 |
| 3 | 30 | 30 | 497 | 47.69% | $1,754.00 | -$1,276.00 | 8 | -$924.50 |
| 4 | 40 | 30 | 489 | 41.92% | $1,547.00 | -$1,601.50 | 10 | -$1,139.50 |

## Scale Clarification

The sweep uses MNQ-equivalent risk assumptions:

```text
point_value = 2
commission_per_contract = 1
```

The previously documented 40/20 fresh-breakout run now reports both equivalents in `trade_analysis.json`:

| Equivalent | Point Value | Net P&L | Max Drawdown |
| --- | ---: | ---: | ---: |
| MNQ | $2/point | $2,958.00 | -$1,621.00 |
| NQ | $20/point | $38,490.00 | -$14,284.00 |

The NQ equivalent keeps the same commission-dollar model as the trade ledger. Before using this for live sizing, rerun with broker-realistic NQ commissions, slippage, and account risk constraints.

## Interpretation

The first small sweep improved the lead branch:

- 30/20 beat 40/20 on net P&L.
- 30/20 also had lower drawdown, shorter loss streak, and better win rate.
- Wider giveback at 30 points and 40 points weakened robustness.

This suggests the early plateau may be around lower activation and tighter giveback, but four combinations are not enough for promotion. The next run should broaden around the promising area:

- Activation: 25, 30, 35, 40, 45
- Giveback: 15, 20, 25
- Clearance: 0, 0.5
- Cooldown: 0, 3

Promotion should still require positive P&L without best 5 trades, drawdown within MNQ-first risk tolerance, and no single month carrying the result.
