# Lead Candidate Comparison - 2026-07-01

## Purpose

Compare the top three exit-branch candidates from the disciplined sweep using identical data, costs, session, and reporting assumptions.

This is a survivability comparison, not a final strategy-selection memo.

## Important Scope Warning

The current Python baseline strategy is long-only. Every candidate below has only long trades.

That makes this comparison valuable for the long-side breakout/exit branch, but it does not yet represent the full Pine strategy if the intended production thesis requires both longs and shorts.

Short-side parity and short-side research remain required before treating this Python branch as a complete replacement for the Pine strategy.

## Shared Inputs

- Input: `/private/tmp/full_python_selected_stream_20260701/selected_bars.csv`
- Stream: selected NQ contracts
- Span: 2021-03-16 to 2026-03-15
- Session: RTH
- Point value: `2`
- Slippage: `1` point per side
- Commission: `$1` per contract
- Symbol-change exit mode: `previous_close`
- Fresh breakout re-entry gate: enabled

## Candidates

| Candidate | Activation | Giveback | Fresh Breakout Clearance | Cooldown |
| --- | ---: | ---: | ---: | ---: |
| A | 30 | 20 | 0.5 | 0 |
| B | 35 | 25 | 0.5 | 3 |
| C | 30 | 20 | 0.5 | 3 |

## Output Paths

| Candidate | Trades | Analysis |
| --- | --- | --- |
| A | `/private/tmp/full_python_candidate_A_30_20_clearance05_cd0_20260701/trades.csv` | `/private/tmp/full_python_candidate_A_30_20_clearance05_cd0_analysis_20260701/trade_analysis.json` |
| B | `/private/tmp/full_python_candidate_B_35_25_clearance05_cd3_20260701/trades.csv` | `/private/tmp/full_python_candidate_B_35_25_clearance05_cd3_analysis_20260701/trade_analysis.json` |
| C | `/private/tmp/full_python_candidate_C_30_20_clearance05_cd3_20260701/trades.csv` | `/private/tmp/full_python_candidate_C_30_20_clearance05_cd3_analysis_20260701/trade_analysis.json` |

## Headline Comparison

MNQ-equivalent results use `point_value=2`.

| Candidate | Trades | Win Rate | MNQ Net | MNQ Max DD | Max Loss Streak | Without Best 1 | Without Best 3 | Without Best 5 | Without Best 10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 477 | 54.09% | $3,728.50 | -$802.00 | 6 | $3,248.00 | $2,517.00 | $1,853.50 | $473.50 |
| B | 397 | 49.87% | $4,111.00 | -$890.50 | 11 | $3,548.50 | $2,694.50 | $2,004.50 | $499.50 |
| C | 421 | 54.39% | $3,950.50 | -$802.00 | 11 | $3,490.50 | $2,782.50 | $2,119.00 | $792.00 |

## NQ Equivalent

NQ-equivalent results use `point_value=20` while keeping the same dollar commission model as the trade ledger.

| Candidate | NQ Net | NQ Max DD |
| --- | ---: | ---: |
| A | $45,871.00 | -$7,624.00 |
| B | $48,256.00 | -$8,437.00 |
| C | $47,083.00 | -$7,624.00 |

## Monthly Failure Profile

| Candidate | Months | Negative Months | Worst Month | Second Worst | Notes |
| --- | ---: | ---: | --- | --- | --- |
| A | 22 | 6 | 2024-11: -$360.00 | 2021-09: -$355.50 | Best loss-streak comfort |
| B | 22 | 5 | 2021-09: -$375.50 | 2025-07: -$261.50 | Highest net, but longer loss streak |
| C | 22 | 6 | 2021-09: -$355.50 | 2021-10: -$142.00 | Best without-best-10 result |

Shared stress months:

- `2021-09` is bad for all three candidates.
- `2025-07` is negative for all three candidates.
- `2021-10` is negative for all three candidates.

These months should become named failure-cluster fixtures.

## Contract Contribution

Worst contract contributions:

| Candidate | Worst Contract | Net P&L | Trades |
| --- | --- | ---: | ---: |
| A | NQZ4 | -$378.00 | 63 |
| B | NQU1 | -$73.50 | 77 |
| C | NQZ4 | -$55.00 | 49 |

Best contract contributions:

| Candidate | Best Contract | Net P&L | Trades |
| --- | --- | ---: | ---: |
| A | NQM4 | $943.50 | 39 |
| B | NQZ1 | $863.00 | 34 |
| C | NQH4 | $1,052.50 | 46 |

## Interpretation

Candidate A is the most psychologically survivable of the three because its max loss streak is `6`, compared with `11` for B and C. It gives up some top-line net P&L, but it also avoids the deeper sequence pain created by the cooldown branches.

Candidate B is the highest net result, but it pays for that with the worst NQ-equivalent drawdown and an `11` trade max loss streak.

Candidate C is the strongest on best-trade dependency, especially after removing the best 10 trades. It is the most robust by that metric, but the `11` trade max loss streak is a real operational concern.

The most reasonable research posture right now:

1. Keep Candidate A as the survivability-first lead.
2. Keep Candidate C as the robustness challenger.
3. Do not promote Candidate B unless the loss-streak issue is acceptable under the MNQ-first risk model.

## Required Next Step

Add short-side strategy support or explicitly mark the current Python branch as long-only research.

Without short-side coverage, we cannot compare this Python branch to the full original strategy intent.
