# Short Side Support Smoke - 2026-07-01

## Purpose

Add first-class short-side support to the Python simulator and test whether naive symmetric short breakdowns help the current Candidate A branch.

## Implementation Summary

Short-side support is now explicit and opt-in.

- Default behavior remains long-only.
- `--enable-short` enables short-side entries.
- `--disable-long` allows short-only experiments.
- Simulator fills, stops, MFE/MAE, MFE trailing, and fresh breakout re-entry gates are side-aware.

This preserves previous long-only research results while allowing controlled short-side experiments.

## Verification

Focused tests:

- `tests/test_baseline_strategy.py`
- `tests/test_execution_simulator.py`
- `tests/test_cli_trade_simulation.py`
- `tests/test_exit_sweep.py`
- `tests/test_cli_exit_sweep.py`

Latest full-suite result before this note:

- `71 passed in 1.52s`

## Long-Only Preservation Check

Command output path:

- Trades: `/private/tmp/full_python_candidate_A_default_long_only_after_short_support_20260701/trades.csv`
- Analysis: `/private/tmp/full_python_candidate_A_default_long_only_after_short_support_analysis_20260701/trade_analysis.json`

Candidate A remains unchanged when shorts are not explicitly enabled:

| Mode | Trades | Net P&L | Max DD | Max Loss Streak | P&L Without Best 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Default long-only | 477 | $3,728.50 | -$802.00 | 6 | $1,853.50 |

This matches the prior Candidate A result.

## Explicit Both-Side Smoke

Command output path:

- Trades: `/private/tmp/full_python_candidate_A_both_sides_explicit_30_20_clearance05_cd0_20260701/trades.csv`
- Analysis: `/private/tmp/full_python_candidate_A_both_sides_explicit_30_20_clearance05_cd0_analysis_20260701/trade_analysis.json`

Settings:

- Candidate A exit branch
- `activation=30`
- `giveback=20`
- `fresh_breakout_clearance=0.5`
- `cooldown=0`
- `--enable-short`

Headline:

| Mode | Trades | Win Rate | Net P&L | Max DD | Max Loss Streak | P&L Without Best 5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Long + naive symmetric short | 20,877 | 49.05% | -$51,324.00 | -$54,752.50 | 12 | -$57,111.00 |

Side contribution:

| Side | Trades | Wins | Losses | Net P&L | P&L Points |
| --- | ---: | ---: | ---: | ---: | ---: |
| Long | 10,727 | 5,229 | 5,498 | -$27,787.00 | -3,166.50 |
| Short | 10,150 | 5,011 | 5,139 | -$23,537.00 | -1,618.50 |

## Interpretation

The short-side implementation works mechanically, but naive symmetric shorts are not viable.

The big warning is not just that shorts lost money. Enabling the simple mirrored breakdown logic changes the trade frequency from `477` to `20,877`, which means the strategy becomes a churn machine. Both sides lose after costs.

This does not prove that shorts should be abandoned. It proves that shorts need their own permission model, not a naive mirror of the long breakout rule.

## Recommended Next Step

Run short-only diagnostics with stricter permissions:

- `--disable-long --enable-short`
- Wider breakout lookback or stronger body/close-quality filters
- Time-of-day constraints
- Regime permissions, especially volatility and trend-state filters
- Separate short MFE trailing/giveback settings

The right question is now:

"Under what conditions are shorts allowed at all?"

not:

"Should every long breakout rule be mirrored for shorts?"
