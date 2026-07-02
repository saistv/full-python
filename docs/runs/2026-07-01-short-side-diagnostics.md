# Short Side Diagnostics - 2026-07-01

## Question

Why did naive symmetric shorts fail when earlier Pine-style tests suggested both long and short participation could be valuable?

## Runs Compared

All runs used Candidate A exit settings:

- MFE activation: `30`
- MFE giveback: `20`
- Fresh breakout/breakdown clearance: `0.5`
- Cooldown: `0`
- Session: RTH
- Point value: `2`
- Slippage: `1` point per side
- Commission: `$1` per side
- Symbol-change exit mode: `previous_close`

| Run | Direction Flags | Trades Path | Analysis Path |
| --- | --- | --- | --- |
| Long-only | default | `/private/tmp/full_python_candidate_A_default_long_only_after_short_support_20260701/trades.csv` | `/private/tmp/full_python_candidate_A_default_long_only_after_short_support_analysis_20260701/trade_analysis.json` |
| Short-only | `--disable-long --enable-short` | `/private/tmp/full_python_candidate_A_short_only_30_20_clearance05_cd0_20260701/trades.csv` | `/private/tmp/full_python_candidate_A_short_only_30_20_clearance05_cd0_analysis_20260701/trade_analysis.json` |
| Both sides | `--enable-short` | `/private/tmp/full_python_candidate_A_both_sides_explicit_30_20_clearance05_cd0_20260701/trades.csv` | `/private/tmp/full_python_candidate_A_both_sides_explicit_30_20_clearance05_cd0_analysis_20260701/trade_analysis.json` |

## Headline Results

| Run | Trades | Win Rate | Net P&L | Max DD | Max Loss Streak | Avg Net/Trade |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Long-only | 477 | 54.09% | $3,728.50 | -$802.00 | 6 | $7.82 |
| Short-only | 81 | 49.38% | -$488.00 | -$535.50 | 4 | -$6.02 |
| Both sides | 20,877 | 49.05% | -$51,324.00 | -$54,752.50 | 12 | -$2.46 |

## Exit Math

Long-only:

- MFE trailing winners: `256`, net `$18,138.50`
- Stop losers: `219`, net `-$14,454.00`
- Average winning trailing exit: about `$70.85`
- Average stopped loser: `-$66.00`

Short-only:

- MFE trailing winners: `40`, net `$2,218.00`
- Stop losers: `41`, net `-$2,706.00`
- Average winning trailing exit: about `$55.45`
- Average stopped loser: `-$66.00`

The short side has a worse payoff shape under mirrored settings. Its winners are smaller, while its stopped losers are the same size as long losers. That means the short side needs a higher win rate or a different exit/stop model. It has neither here.

## Trade Frequency And Gate Behavior

The larger issue is not just short expectancy. It is interaction.

| Run | Months With Trades | Median Gap Between Trades | Gaps <= 5 Minutes |
| --- | ---: | ---: | ---: |
| Long-only | 22 | 13 minutes | 144 / 476 |
| Short-only | 5 | 11 minutes | 23 / 80 |
| Both sides | 61 | 2 minutes | 15,956 / 20,876 |

Short-only only traded in:

- `2021-03`
- `2022-05`
- `2022-06`
- `2022-09`
- `2022-10`

This is because the one-direction fresh-breakdown gate demands a meaningful fresh low after a short exit. In an upward-drifting index, that rarely happens after early bear/chop windows.

Both-side mode behaves very differently. Opposite-side trades keep resetting the post-exit high/low range, so the strategy does not wait for a major fresh low or major fresh high. It flips between local two-bar breakouts and breakdowns. That turns the strategy into a high-frequency churn machine:

- `20,877` trades
- `12,409` side flips
- `59.44%` of adjacent trades flip direction
- median gap between trades: `2` minutes

## Root Cause

The current Python baseline is much simpler than the Pine-style system. It only uses a two-bar breakout/breakdown plus stop/trailing mechanics. It does not yet include the full short-side permission stack from the original strategy, such as:

- support/resistance quality
- ATF trend state
- squeeze/momentum context
- prove-it logic
- time-of-day permissions
- regime filters
- breakout quality vetoes

So "long rules reversed for shorts" is not actually equivalent to the older Pine condition set. It is only reversing the primitive placeholder breakout engine.

The specific failure has two layers:

1. **Short-only expectancy weakness:** mirrored short winners are smaller than long winners while stopped losses remain about the same size.
2. **Both-side interaction failure:** enabling both directions lets opposite-side trades reset the fresh breakout/breakdown gate, creating rapid local flip trades instead of selective directional setups.

## What This Means

Shorts should not be abandoned, but they must not use the same naive permission model as longs.

The old intuition may still be right: shorts can help when criteria line up. The problem is that the current Python baseline does not yet define those criteria. It allows shorts because price broke a two-bar low, which is far too permissive.

## Recommended Next Research Branch

Build explicit short permissions instead of mirrored permissions:

1. **Short-only diagnostic mode:** keep `--disable-long --enable-short` as the default way to study shorts.
2. **Add short-side filters one at a time:** trend/regime, time window, support/resistance breakdown quality, and momentum confirmation.
3. **Prevent cross-side churn:** add an optional side-flip cooldown or require same-side freshness independent of opposite-side exits.
4. **Optimize short exits separately:** test lower activation/giveback settings and tighter invalidation, because short winners are currently smaller.

The next practical question:

> Which short permission removes most of the 10,150 naive short trades while keeping the small subset that behaves like true downside expansion?

