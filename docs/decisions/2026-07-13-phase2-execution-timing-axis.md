# Phase 2 NQ Execution-Timing Axis

> **Superseded correction (2026-07-13):** the delayed-entry rows below were
> contaminated by fill-time stops landing on the profitable side of the actual
> entry. Do not use those rows. See
> `2026-07-13-phase0-audit-follow-up.md` for corrected results.

**Decision:** the locked edge remains profitable under one-minute added
latency, deterministic 10% missed entries, and their combination. The timing
hypothesis passes on total net, but latency materially worsens forward-fold
consistency. No timing change is promoted.

## Registered Design

- Experiment: `phase2-nq-execution-timing-axis-v1`
- Trial budget: 4
- Source: clean commit `bb11c4e`
- Costs: 0.75 points per side, $10 round-trip commission
- Miss seed: `20260713`
- Hypothesis: total net remains positive in all scenarios

`entry_delay_bars=1` means one additional completed one-minute bar beyond the
normal next-bar-open fill. `entry_fill_rate=0.90` uses a stable hash of the
intent identity and seed; it represents missed signal delivery, not a claim
that market orders have a 90% fill probability.

## Aggregate Results

| Scenario | Trades | Missed | Net P&L | PF | Max DD | Positive forward folds |
|---|---:|---:|---:|---:|---:|---:|
| Reference | 813 | 0 | $160,125 | 1.420 | -$18,570 | 5/7 |
| One-minute latency | 829 | 0 | $161,205 | 1.439 | -$18,100 | 3/7 |
| 10% missed | 743 | 78 | $153,200 | 1.439 | -$12,960 | 5/7 |
| Latency + missed | 756 | 80 | $143,595 | 1.427 | -$14,740 | 4/7 |

All scenarios remain profitable, and all remain positive after removing their
top ten trades. The combined scenario retains `$51,980` after that removal.

## Forward-Fold Finding

One-minute latency appears slightly better in aggregate but is less stable:

- reference: 5/7 positive folds;
- latency: 3/7 positive folds;
- latency turns 2024 H1 and 2025 H2 from small gains into losses;
- latency does not repair either losing 2023 half;
- its aggregate improvement comes from larger gains in already-strong 2024 H2,
  2025 H1, and 2026 H1.

This is another right-tail concentration effect. Aggregate P&L alone would
have falsely made latency look like an improvement.

The 10% omission scenario makes 2023 H1 positive and reduces drawdown, but that
is not actionable alpha. A fixed random omission has no market mechanism and
can remove rare winners just as easily in another seed or future path. It is a
resilience stress only.

## Conclusion

The edge has useful operational tolerance: one delayed minute and roughly 10%
missed signals do not destroy five-year expectancy. However, delayed execution
increases regime concentration. Live monitoring must measure actual signal-to-
ack and signal-to-fill latency; a persistent one-minute delay is unacceptable
even though the historical aggregate survives it.

Artifacts:

- `runs/phase2-nq-execution-timing-axis.json`
- `runs/phase2-timing-experiments-folded.sqlite`

Remaining Phase 2 work: registered component ablation and tick/lower-timeframe
bounds for same-minute entry-stop trades.
