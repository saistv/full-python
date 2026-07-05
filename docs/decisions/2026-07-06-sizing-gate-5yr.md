# Capital Allocation / Sizing Gate — 5-Year Re-Run (2026-07-06)

Re-runs the 1 NQ vs MNQ sizing comparison that
`docs/decisions/2026-07-04-sizing-research-gate.md` explicitly deferred
("Re-run this exact comparison once a multi-year dataset exists" — the
9-month n=115 version was not statistically meaningful). Now run over
the full assembled 5-year window (`runs/multi-year/nq1_2021-03-16_
2026-06-26.csv`, 2021-03-16 → 2026-06-26) with the frozen cost model
and `adaptive_trend_am`. Each candidate changes ONLY point value and
commission; the $1,000 dollar-denominated DLL is held constant across
all (the realistic account-level guard), exactly as the 9-month gate.
Artifacts: `runs/sizing-5yr/` (gitignored, reproducible).

## Results (5-year)

| Candidate | Trades | Net P&L | Max DD | Return/DD | Train | Holdout |
|---|---|---|---|---|---|---|
| **1 NQ** | 829 | **$159,160** | **-$19,775** | **8.05** | $65,855 | $51,790 |
| 1 MNQ | 875 | $15,012 | -$2,274 | 6.60 | $6,580 | $4,784 |
| 3 MNQ | 875 | $45,034 | -$6,824 | 6.60 | $19,740 | $14,352 |
| 5 MNQ | 875 | $75,058 | -$11,372 | 6.60 | $32,900 | $23,920 |

## Finding 1: the 9-month results replicate over 5 years

- **NQ/MNQ net ratio: 10.60×** (9-month gate: 10.62×) — the ~10.6×
  multiplier is stable across the full multi-regime window, not a
  short-sample artifact.
- **Trade-count divergence holds, same direction:** NQ 829 vs MNQ 875
  (9-month: 115 vs 129). MNQ takes ~5-6% MORE trades. Mechanism
  confirmed: the $1,000 DLL is denominated in dollars, so at NQ's
  point_value=20 a $1,000 loss is 50 points (bites often) while at
  MNQ's point_value=2 it is 500 points (essentially never bites
  intra-session). The DLL/projected-risk guard therefore blocks a
  different set of entries at each scale. On MNQ the DLL is effectively
  OFF.
- Both splits positive for every candidate; the sizing relationship is
  stable across train and holdout (not regime-dependent).

## Finding 2 (NEW — the 9-month n=115 could not show this): NQ is more risk-efficient than any MNQ stack

The three MNQ candidates share ONE trade population (875 trades, DLL
inactive) and are exact linear scalings of each other — identical
Return/DD of **6.60**. 1 NQ is a DIFFERENT population (829 trades,
DLL active) with Return/DD **8.05** — ~22% better risk-adjusted return.

The DLL, by truncating ~46 entries at NQ scale, removes bad sequences
and improves the drawdown profile. At MNQ scale the DLL never engages,
so those entries stay and drag Return/DD down. The account guard is
doing exactly its job — but only at NQ's dollar scale.

**Equal-notional corollary:** a 10-MNQ stack (= 1 NQ notional, DLL held
at $1,000) would linearly scale the MNQ population to ≈ $150,120 net /
-$22,740 DD (Return/DD 6.60). **1 NQ beats that on BOTH axes** —
higher net ($159,160) AND shallower drawdown (-$19,775). Replicating
NQ notional with a micro stack is strictly worse, because the
dollar-DLL only bites (beneficially) at NQ scale.

## Decision guidance

- **If the account can absorb 1 NQ's drawdown (~$20K over 5 years, and
  Standard 8: that is a floor, not a bound — worse is possible): trade
  1 NQ.** Best risk efficiency; the validated DLL engages as designed.
- **If a smaller account's drawdown limit forces micros:** the MNQ
  stack scales linearly — pick the contract count that fits the DD
  budget (3 MNQ ≈ -$6.8K DD, 5 MNQ ≈ -$11.4K DD), accepting ~18-22%
  worse Return/DD as the cost of not letting the DLL engage.
- **Do NOT build a ~10-MNQ stack to imitate 1 NQ** — you get less P&L
  and more drawdown than simply trading the NQ.
- This does not reopen the "2 NQ" question (separately rejected: 1.24×
  return for 2.28× prop-DD risk). The axis here is NQ vs micro
  granularity for fitting account constraints, not leverage.

## Caveats (binding)

- Backtest, 1-contract-base, pessimistic frozen cost model — live will
  be worse (slippage/fills/latency). Not a promotion of any config
  change; the strategy config is unchanged.
- Holding the DLL at $1,000 for MNQ is the realistic account-guard
  assumption AND the reason MNQ looks "rawer." A deployment that scaled
  the DLL down with size would change the MNQ numbers (and likely lose
  the DLL's benefit entirely at micro scale). The $1,000 figure is the
  validated NQ guard; see [[reference_supervisor_cap_scaling]] for the
  parallel per-instrument reasoning on the live supervisor cap.
- Prop-account caps clip the right tail regardless of contract choice —
  see the daily-concentration finding (top 5 days = 36% of 5-year P&L);
  the sizing decision does not change that truncation.
