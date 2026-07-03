# MR Variant 1 — VWAP Reversion v0.2-py, Run 1 (hypothesis)

Filed BEFORE the run, per the MR research contract. Variant 1 of 5;
run 1 of a maximum 3 for this variant.

## Hypothesis

Extreme intraday extensions from session VWAP (≥ 2.5 × ATR(14)) on
non-trending days (daily ADX(14) < 20) revert enough to pay a 2:1
static-target trade with a 1-ATR stop, at a 55%+ win rate, producing an
edge with low correlation to Adaptive Trend.

## Design — mapped to the eight contract principles

| # | Principle | v0.2-py implementation | v0.1 violation this fixes |
|---|---|---|---|
| 1 | Static target anchored at entry | target = signal close ∓ 2 × stop distance, frozen; NEVER the (moving) VWAP | moving VWAP target |
| 2 | R:R ≥ 2:1 | exactly 2:1 by construction | 1.3:1 |
| 3 | Tight stop ≤ 1.0 ATR(14) | 1.0 × 1m ATR(14), frozen at entry, engine-held | 1.5 ATR |
| 4 | Short hold, 15–20 bars | hard time stop at 20 bars → exit next open | 30 bars |
| 5 | Strict regime gate | daily ADX(14) < 20, computed from prior sessions only (M4 classifier logic) — decidable at the open | ADX 25 |
| 6 | Extreme entry 2.5–3σ | close beyond VWAP ± 2.5 × ATR(14) band | 2σ |
| 7 | High WR expected | pre-set: WR ≥ 55% or the signal is wrong | — |
| 8 | Exit asymmetry favors wins | stop 1R / target 2R / time stop; no move-against | — |

Additional choices (each documented, each sweepable later):

- **Entry window 10:00–15:30 ET**, backstop 15:59: deliberately avoids
  AT's 9:30–10:00 window, driving fire-day overlap and correlation down
  BY CONSTRUCTION (portfolio criterion: corr within ±0.2, overlap ≤ 30%).
- **VWAP**: RTH-anchored (from 9:30), typical-price (H+L+C)/3, volume-weighted.
- **Re-entry**: allowed after 5 flat bars (cooldown); no daily trade cap.
- **Sizing**: 1 NQ flat (contract: evaluation before AM scaling).
- **Execution**: identical to the reconciled AT runs — next-bar-open
  fills, 0.75pt slippage each way, $10 RT commission, $20/pt.

## Pre-set evaluation criteria (8-month window, Oct 2025 – Jun 2026)

The contract's tiers are 3-year figures; this window is ~22% of that.
Scaled expectations, decided now:

- **Signal validity**: WR ≥ 55%. Below 50% = signal is wrong (principle 7),
  and iteration should attack the entry, not the exits.
- **Don't-give-up trigger** (contract): PF ≥ 1.2 with |t| ≥ 2.0 on trade P&L.
- **Portfolio trigger** (contract): positive net with daily correlation to
  AT within ±0.2 → valuable regardless of standalone size.
- **Floor-tier pace**: net ≳ $33K over the window (≈ $150K/3yr pro-rated).
- A losing run 1 does NOT kill the variant (contract: up to 3 runs;
  non-criteria explicitly include "a single losing run ≠ dead hypothesis").

## What run 1 is for

Run 1 is the literature-faithful baseline. Its job is to locate which
component (entry extremity, regime gate, exit geometry, window) needs
iteration in runs 2–3 — not to hit the ceiling on the first attempt.
Sweeps come AFTER the baseline verdict, on axes chosen from its failure
mode, not shotgunned.
