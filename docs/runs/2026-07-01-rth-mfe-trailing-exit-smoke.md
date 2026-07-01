# RTH MFE Trailing Exit Smoke

Date: 2026-07-01

Input bar stream:

```text
/private/tmp/full_python_selected_stream_20260701/selected_bars.csv
```

Trade ledger command:

```bash
PYTHONPATH=src python3 -m full_python.cli simulate-baseline-trades \
  --data /private/tmp/full_python_selected_stream_20260701/selected_bars.csv \
  --output-dir /private/tmp/full_python_rth_mfe_trailing_40_20_trade_ledger_20260701 \
  --stream-input \
  --session rth \
  --point-value 2 \
  --slippage-points-per-side 1 \
  --commission-per-contract 1 \
  --symbol-change-exit-mode previous_close \
  --mfe-trailing-activation-points 40 \
  --mfe-trailing-giveback-points 20
```

Analysis command:

```bash
PYTHONPATH=src python3 -m full_python.cli analyze-trades \
  --trades /private/tmp/full_python_rth_mfe_trailing_40_20_trade_ledger_20260701/trades.csv \
  --output-dir /private/tmp/full_python_rth_mfe_trailing_40_20_trade_analysis_20260701
```

## Assumptions

- Session: RTH
- Point value: $2.00 per point
- Slippage: 1.0 point per side
- Commission: $1.00 per contract per side
- Symbol-change exit mode: `previous_close`
- Exit conversion: `mfe_trailing`
- MFE trailing activation: 40 points
- MFE trailing giveback: 20 points
- Trailing stop applies from completed-bar MFE, not same-bar high/low ordering.

## Headline Comparison

| Metric | Previous-Close Control | MFE Trail 40/20 |
| --- | ---: | ---: |
| Trade count | 737 | 15,059 |
| Win rate | 3.12% | 42.42% |
| Total net P&L | $17,110.50 | -$20,434.00 |
| Max drawdown | -$12,310.50 | -$24,662.50 |
| Max loss streak | 92 | 14 |
| P&L without best 5 trades | -$12,974.50 | -$26,010.00 |

## Exit Reason Breakdown

| Exit Reason | Trades | Winners | Losers | Net P&L |
| --- | ---: | ---: | ---: | ---: |
| mfe_trailing_stop | 6,379 | 6,379 | 0 | $550,626.50 |
| stop | 8,662 | 0 | 8,662 | -$571,692.00 |
| symbol_change | 17 | 8 | 9 | $576.00 |
| end_of_data | 1 | 1 | 0 | $55.50 |

## Interpretation

The MFE trailing rule successfully converts favorable movement into profitable exits, but it also exits positions much earlier and allows many more re-entries. That re-entry explosion overwhelms the gains:

- Control run: 737 trades.
- MFE trailing run: 15,059 trades.
- Converted exits were strongly positive.
- New stop losses created by frequent re-entry were larger than converted gains.

This is a useful failure. It says the exit conversion mechanism works, but it should not be evaluated alone. The next branch should add re-entry discipline before judging MFE trailing:

- Cooldown after exit.
- One trade per direction per breakout structure.
- Re-entry only after a fresh support/resistance breakout.
- Stricter MFE activation and wider giveback sweep.
- Time-window restriction for re-entry after a converted exit.

Do not promote the 40/20 MFE trailing configuration. Treat it as evidence that profit capture can improve, but churn control is mandatory.
