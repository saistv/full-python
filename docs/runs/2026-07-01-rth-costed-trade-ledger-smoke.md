# RTH Costed Trade Ledger Smoke

Date: 2026-07-01

Branch: `codex/real-data-baseline-report`

## Purpose

Run the first-pass baseline trade simulator against the selected-contract stream using full RTH filtering and explicit MNQ-style cost assumptions.

This is still not a strategy verdict. The baseline strategy and exits remain primitive. This run proves the infrastructure can apply session filtering, slippage, commissions, and point value consistently.

## Command

```bash
PYTHONPATH=src python3 -m full_python.cli simulate-baseline-trades \
  --data /private/tmp/full_python_selected_stream_20260701/selected_bars.csv \
  --output-dir /private/tmp/full_python_rth_costed_trade_ledger_20260701 \
  --stream-input \
  --session rth \
  --point-value 2 \
  --slippage-points-per-side 1 \
  --commission-per-contract 1
```

## Outputs

```text
/private/tmp/full_python_rth_costed_trade_ledger_20260701/trades.csv
/private/tmp/full_python_rth_costed_trade_ledger_20260701/trade_summary.json
```

## Assumptions

```text
session: rth
position_model: one_open_long_position_max
entry_fill: current_bar_close
stop_fill: stop_price_when_later_bar_low_touches_stop
symbol_change_exit: new_contract_bar_open
final_exit: last_bar_close
point_value: 2.0
slippage_points_per_side: 1.0
commission_per_contract: 1.0
```

RTH means `09:30 <= America/New_York bar start < 16:00`.

## Summary

```text
trade_count: 737
winning_trades: 23
losing_trades: 714
flat_trades: 0
win_rate: 3.1208%
total_pnl_points: 12878.0
average_pnl_points: 17.4735
total_gross_pnl_dollars: 25756.0
total_commission_dollars: 1474.0
total_net_pnl_dollars: 24282.0
average_net_pnl_dollars: 32.9471
ignored_order_intents: 91774
```

Exit reason counts:

```text
stop: 713
symbol_change: 23
end_of_data: 1
```

First trade:

```text
NQM1 long
entry: 2021-03-16T13:32:00Z @ 13171.0
exit: 2021-03-16T18:22:00Z @ 13139.0
reason: stop
net_pnl_dollars: -66.0
```

Last trade:

```text
NQH6 long
entry: 2026-03-09T14:14:00Z @ 24358.5
exit: 2026-03-13T19:59:00Z @ 24397.0
reason: end_of_data
net_pnl_dollars: 75.0
```

## Finding

RTH filtering and costs materially reduce event volume and make the trade ledger closer to live validation assumptions. The result is still structurally dominated by primitive exit behavior: almost every trade stops out, while a small number of long-held winners drive total P&L.

The next milestone should add monthly/quarterly breakdown, top-trade dependency, and max drawdown/loss-streak metrics before any strategy interpretation.
