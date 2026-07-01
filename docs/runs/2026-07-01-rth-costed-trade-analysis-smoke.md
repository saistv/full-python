# RTH Costed Trade Analysis Smoke

Date: 2026-07-01

Input trade ledger:

```text
/private/tmp/full_python_rth_costed_trade_ledger_20260701/trades.csv
```

Command:

```bash
PYTHONPATH=src python3 -m full_python.cli analyze-trades \
  --trades /private/tmp/full_python_rth_costed_trade_ledger_20260701/trades.csv \
  --output-dir /private/tmp/full_python_rth_costed_trade_analysis_20260701
```

Output:

```text
/private/tmp/full_python_rth_costed_trade_analysis_20260701/trade_analysis.json
```

## Headline Metrics

- Trade count: 737
- Winning trades: 23
- Losing trades: 714
- Win rate: 3.12%
- Total net P&L: $24,282.00
- Average net P&L: $32.95
- Max drawdown: -$12,035.00
- Max loss streak: 90 trades

## Top-Trade Dependency

- Best trade: $10,372.00
- P&L without best 1 trade: $13,910.00
- P&L without best 3 trades: $2,276.50
- P&L without best 5 trades: -$8,740.00

This is not a robust strategy result. The baseline remains highly dependent on a small number of large winners.

## Exit Reason Breakdown

| Exit Reason | Trades | Winners | Losers | Net P&L |
| --- | ---: | ---: | ---: | ---: |
| stop | 713 | 0 | 713 | -$47,058.00 |
| symbol_change | 23 | 22 | 1 | $71,265.00 |
| end_of_data | 1 | 1 | 0 | $75.00 |

The symbol-change exit is carrying the test. That is a research warning, not a victory. A production candidate cannot rely on roll-boundary behavior as the primary source of edge.

## Worst Months

| Month | Trades | Winners | Losers | Net P&L |
| --- | ---: | ---: | ---: | ---: |
| 2022-05 | 42 | 0 | 42 | -$2,772.00 |
| 2022-01 | 36 | 0 | 36 | -$2,376.00 |
| 2022-04 | 34 | 0 | 34 | -$2,244.00 |
| 2024-04 | 34 | 0 | 34 | -$2,244.00 |
| 2025-03 | 54 | 1 | 53 | -$2,111.50 |

## Best Months

| Month | Trades | Winners | Losers | Net P&L |
| --- | ---: | ---: | ---: | ---: |
| 2025-06 | 10 | 1 | 9 | $9,778.00 |
| 2023-06 | 11 | 1 | 10 | $5,170.50 |
| 2025-09 | 12 | 1 | 11 | $5,077.00 |
| 2024-06 | 11 | 1 | 10 | $4,947.00 |
| 2023-12 | 2 | 1 | 1 | $4,462.00 |

## Interpretation

This smoke confirms the analysis command works and also confirms the baseline is only an instrumentation benchmark. It is useful because it exposes the shape of the strategy:

- The stop behavior is consistently costly.
- Large outlier winners dominate total P&L.
- Loss streaks are not operationally acceptable.
- Roll/symbol-change handling must be isolated from true signal edge before using the result for strategy decisions.
