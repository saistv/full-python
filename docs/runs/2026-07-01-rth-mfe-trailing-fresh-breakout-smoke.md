# RTH MFE Trailing With Fresh-Breakout Re-Entry Smoke

Date: 2026-07-01

Input bar stream:

```text
/private/tmp/full_python_selected_stream_20260701/selected_bars.csv
```

Main trade ledger command:

```bash
PYTHONPATH=src python3 -m full_python.cli simulate-baseline-trades \
  --data /private/tmp/full_python_selected_stream_20260701/selected_bars.csv \
  --output-dir /private/tmp/full_python_rth_mfe_trailing_40_20_fresh_breakout_trade_ledger_20260701 \
  --stream-input \
  --session rth \
  --point-value 2 \
  --slippage-points-per-side 1 \
  --commission-per-contract 1 \
  --symbol-change-exit-mode previous_close \
  --mfe-trailing-activation-points 40 \
  --mfe-trailing-giveback-points 20 \
  --require-fresh-breakout-after-exit
```

Analysis command:

```bash
PYTHONPATH=src python3 -m full_python.cli analyze-trades \
  --trades /private/tmp/full_python_rth_mfe_trailing_40_20_fresh_breakout_trade_ledger_20260701/trades.csv \
  --output-dir /private/tmp/full_python_rth_mfe_trailing_40_20_fresh_breakout_trade_analysis_20260701
```

## Assumptions

- Session: RTH
- Point value: $2.00 per point
- Slippage: 1.0 point per side
- Commission: $1.00 per contract per side
- Symbol-change exit mode: `previous_close`
- Exit conversion: MFE trailing 40/20
- Re-entry control: require fresh breakout after exit
- Fresh breakout rule: while flat after an exit, track the highest high; re-entry requires close above that high plus clearance.
- Clearance: 0.0 points for the main run

## Headline Comparison

| Metric | Previous-Close Control | MFE 40/20 | MFE 40/20 + Cooldown 10 | MFE 40/20 + Fresh Breakout |
| --- | ---: | ---: | ---: | ---: |
| Trade count | 737 | 15,059 | 10,810 | 495 |
| Win rate | 3.12% | 42.42% | 42.30% | 45.05% |
| Total net P&L | $17,110.50 | -$20,434.00 | -$18,127.00 | $2,958.00 |
| Max drawdown | -$12,310.50 | -$24,662.50 | -$24,657.50 | -$1,621.00 |
| Max loss streak | 92 | 14 | 11 | 10 |
| P&L without best 5 trades | -$12,974.50 | -$26,010.00 | -$22,962.00 | $1,061.50 |

## Exit Reason Breakdown

| Exit Reason | Trades | Winners | Losers | Net P&L |
| --- | ---: | ---: | ---: | ---: |
| mfe_trailing_stop | 221 | 221 | 0 | $20,724.50 |
| stop | 272 | 0 | 272 | -$17,952.00 |
| symbol_change | 2 | 2 | 0 | $185.50 |

## Clearance Check

| Metric | Fresh Breakout 0.0 | Fresh Breakout 1.0 |
| --- | ---: | ---: |
| Trade count | 495 | 468 |
| Win rate | 45.05% | 43.80% |
| Total net P&L | $2,958.00 | $1,684.00 |
| Max drawdown | -$1,621.00 | -$1,923.00 |
| Max loss streak | 10 | 9 |
| P&L without best 5 trades | $1,061.50 | -$39.50 |

The 1-point clearance check was worse. It reduced trade count but also removed enough winners that robustness degraded.

## Interpretation

This is the first branch that materially changes the research picture.

- MFE trailing alone proved profit capture is possible but caused severe churn.
- Cooldown reduced churn but did not fix the core problem.
- Fresh-breakout re-entry control reduced churn structurally and kept the run positive.
- Drawdown improved sharply versus the previous-close control.
- P&L without the best 5 trades remained positive in the 0.0 clearance run.

This is still not a viable strategy by itself. Net P&L across five years is too small relative to the target, and this is still a simple baseline entry. But it is a useful direction: exit conversion must be paired with structure-based re-entry permission.

Next branch should sweep the interaction rather than hand-pick one setting:

- MFE activation: 30, 40, 50, 60, 80
- Giveback: 15, 20, 25, 30, 40
- Fresh-breakout clearance: 0.0, 0.5, 1.0
- Optional cooldown after fresh-breakout gate: 0, 3, 5, 10

Promotion should require stable plateaus, positive P&L without best 5 trades, controlled drawdown, and no obvious single-month dependency.
