# Phase 2 NQ Component Ablation

> **SUPERSEDED NUMBERS (2026-07-13).** Every figure in this document was computed
> on a calendar that wrongly treated seven abbreviated CME holiday sessions
> (09:30-13:00 ET, market open) as full closures. See
> `docs/decisions/2026-07-13-exchange-calendar-correction.md` for the corrected
> authority table and TradingView parity restoration. Qualitative conclusions in
> this document survive the correction; the numbers do not.

**Decision:** retain the frozen confirmation stack. Wings and the multi-bar
prove-it hold are strongly supported as defensive filters; squeeze release is
directionally useful. Removing squeeze momentum is near-neutral and therefore
hypothesis-generating only, not promotion-grade evidence.

## Registered Design

- Experiment: `phase2-nq-component-ablation-v1`
- Trial budget: 5
- Clean source commit: `b3c4841`
- Five-year NQ authority data and frozen 0.75-point-per-side cost model
- Trials: reference plus one removal each for squeeze momentum, squeeze
  released state, wings, and the additional prove-it hold
- S/R detection remained mandatory in every scenario
- No combinations, parameter tuning, or holdout selection were permitted

This is a component-necessity diagnosis. The Gate 1 protocol previously closed
these parameters to optimization, so an attractive ablation does not reopen
the axis or permit promotion.

## Results

| Scenario | Trades | Net P&L | PF | Max DD | Loss streak | Exp/trade | Positive folds |
|---|---:|---:|---:|---:|---:|---:|---:|
| Reference | 813 | $160,125 | 1.420 | -$18,570 | 22 | $196.96 | 5/7 |
| No squeeze momentum | 829 | $163,060 | 1.420 | -$15,495 | 23 | $196.69 | 5/7 |
| No squeeze release | 846 | $153,220 | 1.388 | -$17,735 | 20 | $181.11 | 4/7 |
| No wings | 1,074 | $142,600 | 1.298 | -$30,560 | 21 | $132.77 | 4/7 |
| No prove-it hold | 988 | $120,895 | 1.285 | -$33,025 | 36 | $122.36 | 5/7 |

## Interpretation

### Wings

Removing wings admitted 261 additional trades but lost $17,525, reduced PF,
and worsened drawdown by $11,990. The damage appears in both 2023 halves and
turns 2025 H2 negative. Wings is not cosmetic; it rejects weak break candles
that dilute the right-tail edge.

### Prove-it hold

Removing the additional hold bar admitted 175 trades, lost $39,230, worsened
drawdown by $14,455, and increased the maximum loss streak from 22 to 36. The
initial S/R break was still mandatory, so this isolates the value of requiring
the break to hold. This is the strongest component evidence in the run.

### Squeeze release

Removing release admitted 33 trades and lost $6,905. It reduced forward-fold
consistency from 5/7 to 4/7 and made 2024 H1 slightly negative. The effect is
smaller than wings or prove-it but supports retaining the gate.

### Squeeze momentum

Removing momentum added 16 trades and $2,935 while preserving 5/7 positive
folds and improving observed drawdown. However, expectancy per trade was flat
to slightly lower, the maximum loss streak increased by one, P&L after removing
the top ten trades fell by $1,005, short P&L fell by $3,945, and the aggregate
gain is below the locked $10,000 materiality threshold. It is not evidence for
a production change and the holdout remains untouched.

## Conclusion

The strategy is not merely an S/R break with arbitrary decorations. Wings and
prove-it materially protect it from low-quality breakouts; squeeze release adds
smaller but consistent selectivity. Keep all production switches enabled.
Squeeze momentum may be revisited only under a newly pre-registered mechanism
with a valid train/holdout question, not because this diagnostic row happened
to finish slightly higher.

Artifacts (gitignored):

- `runs/phase2-nq-component-ablation-v1.json`
- `runs/phase2-component-ablation-v1.sqlite`

