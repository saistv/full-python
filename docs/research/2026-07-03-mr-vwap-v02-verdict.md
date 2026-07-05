# MR Variant 1 — VWAP Reversion v0.2-py: CLOSED (no deployable edge on this window)

Three runs, each with a hypothesis and pre-set criteria filed before
execution. Verdict per the run-3 pre-declared rule: no sweep cell reached
PF ≥ 1.2 with n ≥ 40, so the variant closes. This is a variant verdict,
not an MR verdict — contract give-up requires all five variants.

## The mechanism chain (the asset this variant leaves behind)

1. **Run 1** (2.5 × ATR(1m) bands): 1,156 trades, WR 32.7%, PF 0.76,
   t = −4.0. The extremity unit was mis-scaled — 1m-ATR multiples are
   crossed by ordinary drift several times a day. Reliably negative:
   fading ordinary NQ drift LOSES with significance.
2. **Run 2** (volume-weighted VWAP-sigma bands, one change): 62 trades,
   WR 38.7%, PF 0.92, t = −0.30. Correctly-scaled 2.5σ extremes are a
   statistical coin flip: ≈ +8R gross across the window, fully consumed
   by ~0.2R/trade of friction ($40 vs a $160–200 stop).
3. **Run 3** (pre-declared calibration sweep, 13 cells): monotone
   improvement with extremity (2.5σ → 3.0σ: PF 0.92 → 1.96) but trade
   count collapses 62 → 8. At the extremity where the fade works, this
   window offers ~1 trade/month — directionally interesting, economically
   empty. Stricter ADX made it worse; time stop 15 mildly better; stop
   size and R:R flat.
4. **No directional asymmetry**: long fades t = +0.09, short fades
   t = −0.72 (n = 43/19) — no variant-split sub-strategy trigger.
5. **The portfolio machinery passed throughout**: daily correlation with
   AT −0.04 to +0.02 across all runs — the disjoint-window design
   delivers structural decorrelation. Reusable for every later variant.

## Standing caveats

- 8-month window (Oct 2025 – Jun 2026), one market regime; the contract
  frame is 3 years. Revisit eligibility: when 3-year TV-parity continuous
  data is assembled, this variant may be re-run once at baseline settings
  as a data-window check — not as a new calibration.
- Trade counts in the interesting cells are far below proof thresholds;
  every conclusion above is about THIS window.

## Next per contract

Variant 2 — **opening range fade** (fade OR breakouts that fail to extend
within N bars; back-into-range targets). Architecturally distinct: breakout
failure, not band reversion. Requires its own pre-filed hypothesis doc.
