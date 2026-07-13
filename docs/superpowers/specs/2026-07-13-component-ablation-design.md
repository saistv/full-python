# Phase 2 Component Ablation Design

## Purpose

Measure whether each confirmation layer in the frozen Adaptive Trend entry
stack contributes useful selectivity. This is a diagnostic necessity test, not
a parameter sweep and not permission to optimize fields closed by the Gate 1
protocol.

## Invariants

- Pivot support/resistance break detection remains mandatory in every trial.
- ATF direction, MA trend alignment, cooldowns, stops, exits, sizing, DLL, data,
  and costs remain frozen.
- Each trial removes exactly one confirmation from the reference config.
- No combinations are tested and no setting is selected from these results.
- The five-trial budget is registered before results are generated.

## Trials

1. Frozen reference.
2. Squeeze momentum/acceleration gate removed.
3. Squeeze released-state gate removed.
4. Wings strong-candle gate removed.
5. Multi-bar prove-it hold removed; the initial S/R break bar is still required.

## Evidence

Each trial reports survivability metrics and all seven anchored six-month
forward folds. Interpretation focuses on marginal trade count, net P&L,
drawdown, long/short contribution, right-tail dependency, and fold consistency.
An ablated trial outperforming the reference is hypothesis-generating only.

