# RTH Previous-Close Roll Exit And Excursion Smoke

Date: 2026-07-01

Input bar stream:

```text
/private/tmp/full_python_selected_stream_20260701/selected_bars.csv
```

Trade ledger command:

```bash
PYTHONPATH=src python3 -m full_python.cli simulate-baseline-trades \
  --data /private/tmp/full_python_selected_stream_20260701/selected_bars.csv \
  --output-dir /private/tmp/full_python_rth_previous_close_trade_ledger_20260701 \
  --stream-input \
  --session rth \
  --point-value 2 \
  --slippage-points-per-side 1 \
  --commission-per-contract 1 \
  --symbol-change-exit-mode previous_close
```

Analysis command:

```bash
PYTHONPATH=src python3 -m full_python.cli analyze-trades \
  --trades /private/tmp/full_python_rth_previous_close_trade_ledger_20260701/trades.csv \
  --output-dir /private/tmp/full_python_rth_previous_close_trade_analysis_20260701
```

Outputs:

```text
/private/tmp/full_python_rth_previous_close_trade_ledger_20260701/trades.csv
/private/tmp/full_python_rth_previous_close_trade_ledger_20260701/trade_summary.json
/private/tmp/full_python_rth_previous_close_trade_analysis_20260701/trade_analysis.json
```

## Purpose

The prior RTH costed baseline exited open trades on contract changes at the new contract bar open. That can import roll-gap behavior into strategy P&L. This run exits symbol-change trades at the previous contract's last close instead.

## Assumptions

- Session: RTH
- Point value: $2.00 per point
- Slippage: 1.0 point per side
- Commission: $1.00 per contract per side
- Symbol-change exit mode: `previous_close`
- Symbol-change exit price rule: `previous_contract_last_close`

## Headline Comparison

| Metric | Next-Open Roll Exit | Previous-Close Roll Exit |
| --- | ---: | ---: |
| Trade count | 737 | 737 |
| Win rate | 3.12% | 3.12% |
| Total net P&L | $24,282.00 | $17,110.50 |
| Max drawdown | -$12,035.00 | -$12,310.50 |
| Max loss streak | 90 | 92 |
| P&L without best 5 trades | -$8,740.00 | -$12,974.50 |

The prior roll handling inflated net P&L by $7,171.50 versus previous-close roll exits.

## Exit Reason Breakdown

| Exit Reason | Trades | Winners | Losers | Net P&L |
| --- | ---: | ---: | ---: | ---: |
| stop | 713 | 0 | 713 | -$47,058.00 |
| symbol_change | 23 | 22 | 1 | $64,093.50 |
| end_of_data | 1 | 1 | 0 | $75.00 |

The system is still carried by a small number of very large non-stop exits. This remains a research benchmark, not a viable candidate.

## Stopped-Trade Excursion

| Metric | Value |
| --- | ---: |
| Stopped trades | 713 |
| Average MFE | 94.35 points |
| Max MFE | 2,026.75 points |
| Average MAE | -45.58 points |
| Worst MAE | -969.50 points |

This is the most useful finding in the run. Many trades that eventually stopped out had meaningful favorable movement first. The next research branch should test whether MFE gates, trailing exits, partial exits, or time-based decay can harvest that movement before full giveback.

## Interpretation

- Roll-gap control reduced headline P&L and made robustness worse.
- Stop exits remain structurally damaging.
- The baseline appears to have signal movement but poor profit capture.
- The next branch should not optimize entries first. It should analyze and test exit conversion: MFE trigger, trailing stop, partial profit, time stop, and contract scaling only after proof.
