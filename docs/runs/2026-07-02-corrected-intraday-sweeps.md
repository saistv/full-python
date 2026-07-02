# Corrected Intraday Sweeps - 2026-07-02

## Purpose

Rerun the prior long-side and short-side exit sweeps under the corrected intraday-flat assumption.

All results in this note use `--exit-at-session-end`.

## Why This Matters

Earlier sweeps filtered to RTH bars but allowed trades to carry across RTH session boundaries. That meant prior candidates could capture overnight or weekend gaps without explicitly modeling non-RTH risk.

This report treats the strategy as intraday-flat.

## Shared Assumptions

- Input: `/private/tmp/full_python_selected_stream_20260701/selected_bars.csv`
- Session: RTH
- Point value: `2`
- Slippage: `1` point per side
- Commission: `$1` per side
- Symbol-change exit mode: `previous_close`
- Fresh breakout/breakdown gate: enabled
- Session-end exit: enabled

## Corrected Long-Side Sweep

Output:

- JSON: `/private/tmp/full_python_long_exit_sweep_session_end_20260702/sweep_results.json`
- CSV: `/private/tmp/full_python_long_exit_sweep_session_end_20260702/sweep_results.csv`

Grid:

- Activations: `25, 30, 35, 40, 45`
- Givebacks: `15, 20, 25`
- Clearances: `0, 0.5`
- Cooldowns: `0, 3`
- Combinations: `60`

Result:

- Positive combinations: `0 / 60`

Top five:

| Rank | Activation | Giveback | Clearance | Cooldown | Trades | Win Rate | Net P&L | Max DD | Max Loss Streak | P&L Without Best 5 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 25 | 15 | 0 | 3 | 539 | 56.96% | -$1,315.00 | -$2,251.00 | 6 | -$2,022.50 |
| 2 | 25 | 20 | 0.5 | 3 | 515 | 55.34% | -$1,346.00 | -$2,231.00 | 8 | -$2,560.00 |
| 3 | 25 | 15 | 0 | 0 | 624 | 56.41% | -$1,800.00 | -$2,507.00 | 6 | -$2,504.50 |
| 4 | 25 | 15 | 0.5 | 3 | 521 | 55.28% | -$1,810.00 | -$2,724.50 | 6 | -$2,517.50 |
| 5 | 25 | 15 | 0.5 | 0 | 587 | 56.22% | -$1,818.50 | -$2,756.00 | 6 | -$2,525.00 |

Average by activation:

| Activation | Avg Net | Avg Without Best 5 | Avg Max DD | Positive Combos |
| ---: | ---: | ---: | ---: | ---: |
| 25 | -$2,383.04 | -$3,593.38 | -$3,044.62 | 0 / 12 |
| 30 | -$2,855.29 | -$4,084.29 | -$3,480.25 | 0 / 12 |
| 35 | -$3,456.54 | -$4,703.12 | -$4,197.00 | 0 / 12 |
| 40 | -$2,771.54 | -$4,043.96 | -$3,540.25 | 0 / 12 |
| 45 | -$3,247.21 | -$4,549.21 | -$4,025.21 | 0 / 12 |

## Corrected Short-Side Sweep

Output:

- JSON: `/private/tmp/full_python_short_only_exit_sweep_session_end_20260702/sweep_results.json`
- CSV: `/private/tmp/full_python_short_only_exit_sweep_session_end_20260702/sweep_results.csv`

Grid:

- Activations: `15, 20, 25, 30, 35, 40`
- Givebacks: `10, 15, 20, 25`
- Clearances: `0, 0.5`
- Cooldowns: `0, 3`
- Combinations: `96`

Result:

- Positive combinations: `1 / 96`

Top five:

| Rank | Activation | Giveback | Clearance | Cooldown | Trades | Win Rate | Net P&L | Max DD | Max Loss Streak | P&L Without Best 5 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 20 | 10 | 0.5 | 0 | 90 | 64.44% | $22.50 | -$305.50 | 3 | -$366.50 |
| 2 | 35 | 15 | 0.5 | 3 | 71 | 49.30% | -$63.50 | -$391.50 | 4 | -$615.00 |
| 3 | 35 | 10 | 0.5 | 3 | 72 | 48.61% | -$66.50 | -$449.50 | 4 | -$555.00 |
| 4 | 20 | 10 | 0 | 0 | 94 | 63.83% | -$71.50 | -$332.50 | 3 | -$460.50 |
| 5 | 15 | 15 | 0.5 | 0 | 84 | 66.67% | -$98.50 | -$423.00 | 3 | -$590.00 |

Average by activation:

| Activation | Avg Net | Avg Without Best 5 | Avg Max DD | Positive Combos |
| ---: | ---: | ---: | ---: | ---: |
| 15 | -$289.19 | -$773.66 | -$531.28 | 0 / 16 |
| 20 | -$389.03 | -$891.34 | -$518.28 | 1 / 16 |
| 25 | -$348.75 | -$876.53 | -$574.31 | 0 / 16 |
| 30 | -$460.28 | -$1,007.88 | -$667.97 | 0 / 16 |
| 35 | -$367.41 | -$914.25 | -$636.25 | 0 / 16 |
| 40 | -$608.22 | -$1,185.94 | -$878.69 | 0 / 16 |

## Interpretation

The corrected intraday-flat replay invalidates the prior exit-branch candidates.

Long side:

- No tested long-side exit configuration was profitable.
- The best long configuration still lost `-$1,315.00`.
- This confirms that prior long-side profitability came from session-carry behavior, not a robust intraday breakout edge.

Short side:

- Only one short-side combination was positive, and only by `$22.50`.
- That same result lost `-$366.50` after removing the best five trades.
- Short exits alone do not create a durable intraday short edge.

## Decision

Stop optimizing this primitive two-bar breakout engine as-is.

The current baseline is useful as infrastructure, but not as a viable strategy model.

The next research branch should move from exit tuning to signal permission quality:

1. support/resistance structure
2. breakout/breakdown quality
3. ATF or trend-state permission
4. squeeze/momentum confirmation
5. time-of-day and regime permissions

The next candidate must prove edge in `--exit-at-session-end` mode before it is considered viable for hands-off intraday automation.

