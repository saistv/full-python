# MR Variant 2 — Opening Range Fade, Run 1 (hypothesis + design)

Filed BEFORE the run, per the MR Research Contract
(`memory: project_mr_research_contract`). Variant 2 of 5; run 1 of a
maximum 3 for this variant. Variant 1 (VWAP reversion) closed after its
3-run budget (`docs/research/2026-07-03-mr-vwap-v02-verdict.md`). This
doc is both the pre-filed hypothesis AND the design spec the
implementation plan consumes.

## Hypothesis

An opening-range breakout that EXTENDS beyond the 9:30-10:00 range
(≥ 1 × ATR(14)) on a non-trending day (daily ADX(14) < 20) and then
FAILS — a bar closing back inside the range within a few bars — reverts
enough to pay a 2:1 static-target trade with a 1-ATR stop, at a 55%+
win rate, producing an edge with low correlation to Adaptive Trend.
Architecturally distinct from variant 1: the signal is breakout
FAILURE, not band-distance reversion.

## Design — mapped to the eight contract principles

| # | Principle | v1 implementation |
|---|---|---|
| 1 | Static target anchored at entry | target = signal close ∓ 2 × stop distance, frozen; never a moving level |
| 2 | R:R ≥ 2:1 | exactly 2:1 by construction (ATR bracket) |
| 3 | Tight stop ≤ 1.0 ATR(14) | 1.0 × 1m ATR(14), frozen at entry, engine-held |
| 4 | Short hold, 15–20 bars | hard time stop at 20 bars → exit next open |
| 5 | Strict regime gate | daily ADX(14) < 20, prior-session-decidable at the open |
| 6 | Extreme entry | the failed breakout must have EXTENDED ≥ 1.0 × ATR(14) beyond the OR edge before closing back inside — the OR-fade analog of 2.5σ extremity |
| 7 | High WR expected | pre-set: WR ≥ 55% or the signal is wrong |
| 8 | Exit asymmetry favors wins | stop 1R / target 2R / time stop; no move-against |

## The signal (the new architecture)

- **Opening range:** `or_high` = max high, `or_low` = min low over RTH
  bars in [09:30, 10:00) ET; frozen at 10:00. Recomputed each session.
- **Breakout:** after 10:00, price trades beyond the OR — a bar with
  `high > or_high` (upside) or `low < or_low` (downside) — AND the
  excursion beyond the edge reaches ≥ `breakout_atr_mult` × ATR(14)
  (default 1.0) at some point during the breakout.
- **Failure → entry:** within `failure_window` bars (default 10) of the
  qualifying breakout, a bar CLOSES back inside the OR
  (`or_low < close < or_high`). Fade at that close: failed upside
  breakout → SHORT; failed downside → LONG. A qualifying breakout that
  does not fail within the window is discarded (no entry) and the OR
  can arm again on a fresh extension.
- **Direction:** always toward the range (fade the failed extension).

## Bracket, gate, decorrelation, execution (inherited from the contract + VwapReversion)

- Enter at the close of the close-back-inside bar. Stop = 1.0 × ATR(14)
  frozen; target = 2.0 × ATR(14) static in the reversion direction;
  time stop 20 bars.
- Daily ADX(14) < 20 regime gate (M4 classifier logic, decidable at the
  open from prior sessions).
- **Fade/entry window 10:00–15:30 ET**, backstop 15:59: entries never
  overlap AT's 9:30–10:00 window → structural decorrelation (portfolio
  criteria: corr within ±0.2, fire-day overlap ≤ 30%). Cooldown 5 flat
  bars between entries; no daily trade cap.
- Sizing 1 NQ flat (contract: evaluate before AM scaling).
- Execution identical to the reconciled AT/vwap runs: next-bar-open
  fills, 0.75pt slippage each way, $10 RT commission, $20/pt.

**Sweepable later (documented, not now):** `breakout_atr_mult` (1.0),
`failure_window` (10), fade-window breadth (10:00–15:30 vs a
morning-only 10:00–11:30), `time_stop_bars` (20), `adx_max` (20),
cooldown (5). Run-1 uses the baseline values above.

## Config (new `OpeningRangeFadeConfig`)

```
name = "opening_range_fade_v1"
atr_length = 14
or_start_minutes_et = 570   # 9:30
or_end_minutes_et = 600     # 10:00 (OR forms here)
entry_start_minutes_et = 600    # 10:00, decorrelated from AT
entry_end_minutes_et = 930      # 15:30
breakout_atr_mult = 1.0
failure_window_bars = 10
stop_atr_mult = 1.0
rr_multiple = 2.0
time_stop_bars = 20
adx_length = 14
adx_max = 20.0
cooldown_bars = 5
contracts = 1
tick_size = 0.25
warmup_bars = 100
```

## Evaluation — the methodological upgrade over v0.2

v0.2 was confined to an 8-month window (its verdict's chief caveat).
This variant runs on the assembled 5-year data with a proper split:

- **Primary: 3-year train window 2023-01-01 → 2025-06-30** (the Gate 1
  train window) — real statistical power vs v0.2's 8 months.
- **Holdout 2025-07-01 → 2026-06-26** touched only if run-1 shows an
  edge on train (confirmation, one shot).
- 2021-03 → 2022-12 available as an extended-robustness buffer.

## Pre-set run-1 criteria (decided now, before the run)

Per the contract's don't-give-up / edge-found / give-up triggers:

- **Iterate to run 2 if** any of: PF ≥ 1.2 with |t| ≥ 2.0 (positive
  edge) on train; OR strong directional asymmetry (one side works,
  |t| ≥ 2.0); OR a modest positive edge with daily corr to AT ≤ 0.2.
- **Edge-found (promote to calibration) if** net ≥ $150K/PF ≥ 1.3 on
  the 3-year-equivalent, OR net ≥ $75K with corr ≤ 0.2.
- **Toward closing if** PF < 1.2 AND |t| < 2.0 AND no directional
  asymmetry — but a run-2 design fix (per the mechanism learned) is
  allowed before the variant closes, exactly as variant 1 got 3 runs.
- Report every run: n, WR, PF, net, paired-t vs zero (per-trade and
  session-level), mean/median R-multiple, long/short split, daily
  correlation with AT, max drawdown.

## Honest priors (stated, not acted on)

- Base rate after variant 1: MR on NQ is hard. The contract exists to
  make me TEST rather than pattern-match a prior rejection (LLM Rule 12
  failure mode). The 3-year window gives this the fair shot v0.2 lacked.
- The AT "OR-width sizing modifier" finding is closed, but that concerns
  AT's anti-martingale sizing on low/high-OR days — a different question
  from an independent OR-failure fade. Not evidence against this variant.
