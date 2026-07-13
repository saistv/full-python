# Phase 2 NQ Execution-Cost Axis

**Decision:** the locked NQ edge survives every pre-registered slippage level.
This is strong evidence against the edge being a three-tick fill artifact. It
is not a complete execution model.

## Registered Design

- Experiment: `phase2-nq-execution-cost-axis-v1`
- Trial budget: 4
- Data: corrected five-year continuous NQ series
- Strategy: unchanged `production_am_config()`
- Commission: $10 round trip
- Extra opening slippage: disabled, as in the TradingView parity model
- Hypothesis: total net remains positive at all four levels

| Scenario | Slippage per side |
|---|---:|
| `tv_matched` | 0.75 points |
| `adverse_1pt` | 1.00 point |
| `stress_1_5pt` | 1.50 points |
| `severe_2pt` | 2.00 points |

## Results

| Scenario | Trades | Net P&L | PF | Max DD | Net without top 10 trades |
|---|---:|---:|---:|---:|---:|
| TV matched | 813 | $160,125 | 1.420 | -$18,570 | $62,305 |
| 1.0 point | 813 | $151,850 | 1.392 | -$20,270 | $54,190 |
| 1.5 points | 810 | $135,995 | 1.340 | -$23,670 | $38,655 |
| 2.0 points | 807 | $120,010 | 1.292 | -$27,070 | $22,990 |

The trade count changes at higher friction because realized equity feeds the
dollar-denominated DLL/projected-risk guard. The effect is therefore not a
pure arithmetic haircut, which is why all four scenarios were fully replayed.

## Interpretation

The hypothesis passes. Even at eight ticks per side, total net and net without
the top ten trades remain positive. PF and drawdown degrade monotonically, as
expected. The system has meaningful cost headroom.

This axis does **not** model:

- one-bar or sub-bar latency;
- missed entries;
- stop gaps beyond the existing OHLC rule;
- queue position or partial fills;
- disconnect/retry behavior;
- market-impact growth with larger size.

Those are separate registered execution-timing and broker-recovery questions.
Calling this a complete fill-realism validation would overstate the result.

Artifacts:

- `runs/phase2-nq-execution-cost-axis.json`
- `runs/phase2-cost-experiments.sqlite`

