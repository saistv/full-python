# Short-Only Exit Sweep - 2026-07-02

## Purpose

Test whether short-side weakness is mainly an exit-geometry problem by sweeping MFE trailing activation/giveback settings in short-only mode.

This deliberately isolates exits before adding new short permission filters.

## Data And Assumptions

- Input: `/private/tmp/full_python_selected_stream_20260701/selected_bars.csv`
- Mode: `--disable-long --enable-short`
- Session: RTH
- Point value: `2`
- Slippage: `1` point per side
- Commission: `$1` per side
- Symbol-change exit mode: `previous_close`
- Fresh breakdown gate: enabled

## Grid

- MFE activations: `15, 20, 25, 30, 35, 40`
- MFE givebacks: `10, 15, 20, 25`
- Fresh breakdown clearances: `0, 0.5`
- Cooldowns: `0, 3`
- Total combinations: `96`

Output:

- JSON: `/private/tmp/full_python_short_only_exit_sweep_20260702/sweep_results.json`
- CSV: `/private/tmp/full_python_short_only_exit_sweep_20260702/sweep_results.csv`

## Top Results

| Rank | Activation | Giveback | Clearance | Cooldown | Trades | Win Rate | Net P&L | Max DD | Max Loss Streak | P&L Without Best 5 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 20 | 10 | 0.5 | 0 | 89 | 65.17% | $279.00 | -$305.50 | 3 | -$322.50 |
| 2 | 20 | 10 | 0 | 0 | 93 | 64.52% | $185.00 | -$332.50 | 3 | -$416.50 |
| 3 | 25 | 10 | 0.5 | 0 | 79 | 58.23% | $157.50 | -$481.50 | 5 | -$487.00 |
| 4 | 20 | 15 | 0.5 | 0 | 82 | 64.63% | $142.00 | -$310.50 | 3 | -$598.00 |
| 5 | 25 | 10 | 0 | 0 | 83 | 57.83% | $141.00 | -$518.00 | 3 | -$521.00 |

## Robustness

Only `9 / 96` combinations were net positive.

No tested combination stayed positive after removing the best five trades.

Best result:

- Net P&L: `$279.00`
- P&L without best five trades: `-$322.50`

That means the small short-side profitability depends on a handful of best trades and should not be treated as a durable short edge.

## Parameter Reads

Average by activation:

| Activation | Avg Net | Avg Without Best 5 | Avg Max DD | Positive Combos |
| ---: | ---: | ---: | ---: | ---: |
| 15 | -$297.06 | -$780.53 | -$534.41 | 0 / 16 |
| 20 | -$283.66 | -$888.28 | -$496.53 | 4 / 16 |
| 25 | -$313.56 | -$894.47 | -$574.31 | 3 / 16 |
| 30 | -$429.16 | -$1,052.75 | -$670.59 | 0 / 16 |
| 35 | -$406.06 | -$1,036.97 | -$632.88 | 2 / 16 |
| 40 | -$607.88 | -$1,314.94 | -$836.16 | 0 / 16 |

Average by giveback:

| Giveback | Avg Net | Avg Without Best 5 | Avg Max DD | Positive Combos |
| ---: | ---: | ---: | ---: | ---: |
| 10 | -$418.92 | -$922.79 | -$661.69 | 4 / 24 |
| 15 | -$308.75 | -$916.27 | -$611.69 | 3 / 24 |
| 20 | -$472.67 | -$1,071.15 | -$606.69 | 0 / 24 |
| 25 | -$357.92 | -$1,068.42 | -$616.52 | 2 / 24 |

Average by cooldown:

| Cooldown | Avg Net | Avg Without Best 5 | Avg Max DD | Positive Combos |
| ---: | ---: | ---: | ---: | ---: |
| 0 | -$304.45 | -$995.71 | -$610.39 | 9 / 48 |
| 3 | -$474.68 | -$993.60 | -$637.91 | 0 / 48 |

## Interpretation

Short-side exits matter, but exits are not the main problem.

The best short-only branch used a faster trailing profile:

- Activation: `20`
- Giveback: `10`
- Clearance: `0.5`
- Cooldown: `0`

That makes sense: short moves in NQ appear to need faster profit capture than long moves. But even the best branch fails the best-trade-dependency test.

The current conclusion:

1. Shorts need a different exit profile than longs.
2. Exit tuning alone does not create a robust short edge.
3. Short permission filters are now the priority.

## Next Branch

Run short-only permission diagnostics before another broad parameter sweep.

Candidate filters to test first:

- time-of-day windows
- support/resistance breakdown quality
- momentum confirmation
- trend/regime permission
- side-flip cooldown for both-side mode

The next experiment should answer:

> Which permission filter removes the majority of bad short breakdowns while preserving the small subset that expands quickly enough to justify short risk?

