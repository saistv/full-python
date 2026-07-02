# Exit Branch Disciplined Sweep - 2026-07-01

## Purpose

Expand the small exit-branch sweep around the promising MFE trailing/fresh-breakout area and check whether nearby settings form a robust plateau.

This sweep is still research-only. It is not a production recommendation by itself.

## Data

- Input: `/private/tmp/full_python_selected_stream_20260701/selected_bars.csv`
- Stream: selected NQ contracts
- Span: 2021-03-16 to 2026-03-15
- Rows: 1,769,265
- Session: RTH

## Simulation Assumptions

- Point value: `2`
- Slippage: `1` point per side
- Commission: `$1` per contract
- Symbol-change exit mode: `previous_close`
- MFE trailing logic: completed-bar based
- Fresh breakout re-entry gate: enabled

## Grid

- MFE activation points: `25, 30, 35, 40, 45`
- MFE giveback points: `15, 20, 25`
- Fresh breakout clearance points: `0, 0.5`
- Cooldown bars after exit: `0, 3`
- Total combinations: `60`

## Output

- JSON: `/private/tmp/full_python_exit_branch_sweep_disciplined_20260701/sweep_results.json`
- CSV: `/private/tmp/full_python_exit_branch_sweep_disciplined_20260701/sweep_results.csv`

## Top Results By Ranking

Ranking is total net P&L first, then P&L without best five trades, then drawdown.

| Rank | Activation | Giveback | Clearance | Cooldown | Trades | Win Rate | Net P&L | Max DD | Max Loss Streak | P&L Without Best 5 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 35 | 25 | 0.5 | 3 | 397 | 50% | $4,111.00 | -$890.50 | 11 | $2,004.50 |
| 2 | 30 | 25 | 0.5 | 3 | 400 | 54% | $3,964.00 | -$817.50 | 11 | $1,957.00 |
| 3 | 30 | 20 | 0.5 | 3 | 421 | 54% | $3,950.50 | -$802.00 | 11 | $2,119.00 |
| 4 | 30 | 20 | 0.5 | 0 | 477 | 54% | $3,728.50 | -$802.00 | 6 | $1,853.50 |
| 5 | 45 | 15 | 0 | 0 | 464 | 45% | $3,653.00 | -$1,395.00 | 8 | $1,964.00 |

## Robustness Reads

All 60 combinations were net positive on this selected stream.

The strongest cluster is not a single exact setting. The best area is:

- Activation: `30-35`
- Giveback: `20-25`
- Fresh breakout clearance: `0.5`

Average results by giveback:

| Giveback | Avg Net P&L | Avg P&L Without Best 5 | Avg Max DD | Positive Combos |
| ---: | ---: | ---: | ---: | ---: |
| 15 | $2,178.60 | $507.32 | -$1,086.20 | 20 / 20 |
| 20 | $2,928.70 | $1,095.60 | -$1,050.45 | 20 / 20 |
| 25 | $2,748.10 | $661.58 | -$1,075.08 | 20 / 20 |

Average results by fresh breakout clearance:

| Clearance | Avg Net P&L | Avg P&L Without Best 5 | Avg Max DD | Positive Combos |
| ---: | ---: | ---: | ---: | ---: |
| 0 | $2,323.53 | $456.60 | -$1,090.73 | 30 / 30 |
| 0.5 | $2,913.40 | $1,053.07 | -$1,050.42 | 30 / 30 |

Average results by cooldown:

| Cooldown | Avg Net P&L | Avg P&L Without Best 5 | Avg Max DD | Positive Combos |
| ---: | ---: | ---: | ---: | ---: |
| 0 | $2,710.55 | $822.85 | -$1,122.52 | 30 / 30 |
| 3 | $2,526.38 | $686.82 | -$1,018.63 | 30 / 30 |

## Interpretation

The sweep supports the current research direction. The result is not dependent on one precise parameter value, and the `0.5` fresh-breakout clearance materially improved average net P&L, average P&L without best five trades, and average drawdown.

Giveback `20` is the best average branch in this grid. Giveback `25` produced the highest single result, but weaker average robustness after removing the best five trades.

Cooldown `3` reduced average drawdown, but it did not clearly improve net P&L or loss-streak comfort. The top three ranked results all used cooldown `3`, while the fourth result used cooldown `0` with the same shallow drawdown and a much shorter max loss streak. This needs follow-up before treating cooldown as a default.

## Current Lead Research Candidates

Candidate A, robustness-first:

- Activation: `30`
- Giveback: `20`
- Fresh breakout clearance: `0.5`
- Cooldown: `0`
- Trades: `477`
- Net P&L: `$3,728.50`
- Max DD: `-$802.00`
- Max loss streak: `6`
- P&L without best five trades: `$1,853.50`

Candidate B, P&L-first:

- Activation: `35`
- Giveback: `25`
- Fresh breakout clearance: `0.5`
- Cooldown: `3`
- Trades: `397`
- Net P&L: `$4,111.00`
- Max DD: `-$890.50`
- Max loss streak: `11`
- P&L without best five trades: `$2,004.50`

Candidate C, best without-best-five result:

- Activation: `30`
- Giveback: `20`
- Fresh breakout clearance: `0.5`
- Cooldown: `3`
- Trades: `421`
- Net P&L: `$3,950.50`
- Max DD: `-$802.00`
- Max loss streak: `11`
- P&L without best five trades: `$2,119.00`

## Next Recommended Test

Run a focused comparison of Candidate A, B, and C across contract-year/monthly breakdowns, long-vs-short contribution, and P&L without best `1`, `3`, `5`, and `10` trades.

The immediate question is not "which one made the most." It is whether the smoother Candidate A gives up too much upside, or whether the higher loss-streak candidates are still survivable under the MNQ-first risk target.
