# RTH MFE Trailing With Cooldown Smoke

Date: 2026-07-01

Input bar stream:

```text
/private/tmp/full_python_selected_stream_20260701/selected_bars.csv
```

Trade ledger command:

```bash
PYTHONPATH=src python3 -m full_python.cli simulate-baseline-trades \
  --data /private/tmp/full_python_selected_stream_20260701/selected_bars.csv \
  --output-dir /private/tmp/full_python_rth_mfe_trailing_40_20_cooldown10_trade_ledger_20260701 \
  --stream-input \
  --session rth \
  --point-value 2 \
  --slippage-points-per-side 1 \
  --commission-per-contract 1 \
  --symbol-change-exit-mode previous_close \
  --mfe-trailing-activation-points 40 \
  --mfe-trailing-giveback-points 20 \
  --cooldown-bars-after-exit 10
```

Analysis command:

```bash
PYTHONPATH=src python3 -m full_python.cli analyze-trades \
  --trades /private/tmp/full_python_rth_mfe_trailing_40_20_cooldown10_trade_ledger_20260701/trades.csv \
  --output-dir /private/tmp/full_python_rth_mfe_trailing_40_20_cooldown10_trade_analysis_20260701
```

## Assumptions

- Session: RTH
- Point value: $2.00 per point
- Slippage: 1.0 point per side
- Commission: $1.00 per contract per side
- Symbol-change exit mode: `previous_close`
- Exit conversion: MFE trailing 40/20
- Re-entry control: same-bar exit block plus 10-bar cooldown after every exit

## Headline Comparison

| Metric | Previous-Close Control | MFE Trail 40/20 | MFE Trail 40/20 + Cooldown 10 |
| --- | ---: | ---: | ---: |
| Trade count | 737 | 15,059 | 10,810 |
| Win rate | 3.12% | 42.42% | 42.30% |
| Total net P&L | $17,110.50 | -$20,434.00 | -$18,127.00 |
| Max drawdown | -$12,310.50 | -$24,662.50 | -$24,657.50 |
| Max loss streak | 92 | 14 | 11 |
| P&L without best 5 trades | -$12,974.50 | -$26,010.00 | -$22,962.00 |

## Exit Reason Breakdown

| Exit Reason | Trades | Winners | Losers | Net P&L |
| --- | ---: | ---: | ---: | ---: |
| mfe_trailing_stop | 4,563 | 4,563 | 0 | $392,805.00 |
| stop | 6,235 | 0 | 6,235 | -$411,510.00 |
| symbol_change | 11 | 9 | 2 | $522.50 |
| end_of_data | 1 | 1 | 0 | $55.50 |

## Interpretation

Cooldown helped but did not solve the churn problem.

- Trade count dropped by 4,249 versus MFE trailing without cooldown.
- Max loss streak improved from 14 to 11.
- Net P&L improved by $2,307, but stayed negative.
- The converted trailing exits remained strongly positive.
- The remaining stop losses still overwhelmed converted gains.

This suggests that waiting alone is not enough. The next re-entry control should require a fresh structure event, not just elapsed bars:

- Fresh support/resistance breakout after exit.
- One accepted trade per breakout level.
- Re-entry only if price forms a new local range/pivot.
- MFE trailing plus stricter activation/giveback sweep after structure gating.

Do not promote cooldown 10. Treat it as evidence that churn control helps, but structure-based re-entry gating is probably required.
