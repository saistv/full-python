# First Trade Ledger Smoke

Date: 2026-07-01

Branch: `codex/real-data-baseline-report`

## Purpose

Convert baseline strategy order intents on the selected-contract stream into an explicit first-pass trade ledger. This validates that the project can move from signals/order intents to auditable trades with stated fill assumptions.

This is not a strategy viability result. The strategy is still the placeholder baseline breakout model.

## Command

```bash
PYTHONPATH=src python3 -m full_python.cli simulate-baseline-trades \
  --data /private/tmp/full_python_selected_stream_20260701/selected_bars.csv \
  --output-dir /private/tmp/full_python_trade_ledger_20260701 \
  --stream-input
```

## Outputs

```text
/private/tmp/full_python_trade_ledger_20260701/trades.csv
/private/tmp/full_python_trade_ledger_20260701/trade_summary.json
```

## Assumptions

```text
position_model: one_open_long_position_max
entry_fill: current_bar_close
stop_fill: stop_price_when_later_bar_low_touches_stop
symbol_change_exit: new_contract_bar_open
final_exit: last_bar_close
slippage: none
commission: none
```

## Summary

```text
trade_count: 911
winning_trades: 30
losing_trades: 881
flat_trades: 0
win_rate: 3.2931%
total_pnl_points: 10804.0
average_pnl_points: 11.8595
ignored_order_intents: 317883
```

Exit reason counts:

```text
stop: 877
symbol_change: 33
end_of_data: 1
```

First trade:

```text
NQM1 long
entry: 2021-03-16T00:04:00Z @ 13054.25
exit: 2021-03-17T11:30:00Z @ 13024.25
reason: stop
pnl_points: -30.0
```

Last trade:

```text
NQH6 long
entry: 2026-03-09T02:21:00Z @ 24032.0
exit: 2026-03-15T23:59:00Z @ 24439.5
reason: end_of_data
pnl_points: 407.5
```

## Finding

The first smoke revealed a modeling bug: without an explicit symbol-change exit, one position could remain open across multiple contract rolls and create a fake multi-year winner. The simulator now force-exits at the new contract bar open when the selected stream changes symbol.

The ledger is structurally useful but not yet realistic enough for strategy decisions. The next milestone should add session/RTH filtering, slippage/commission, and more faithful entry/exit rules before any profitability interpretation.
