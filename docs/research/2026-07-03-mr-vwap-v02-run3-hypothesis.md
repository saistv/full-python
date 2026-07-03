# MR Variant 1 — VWAP Reversion v0.2-py, Run 3 (calibration; final for variant)

Filed BEFORE the sweep. Run 3 of 3 — per contract, the calibration run.

## Run 2 verdict (recorded first)

FAIL on signal validity (WR 38.7% vs ≥55%) but materially informative:
trades 1,156 → 62 (sigma fix behaved as predicted), edge moved from
significantly negative (t=−4.0) to statistical zero (PF 0.917, t=−0.30,
n=62). Decomposition: 22 targets × 2R − 36 stops × 1R ≈ +8R GROSS — the
fade is marginally positive before costs. Friction ($40/trade RT+slip)
against a 1×ATR(1m) stop (~$160–200) costs ~0.2R/trade and flips the sign.
Structural decorrelation holds (corr +0.017). Mechanism to attack in run 3:
per-trade edge is too small relative to fixed friction — push entries more
extreme and test the contract's calibration bounds.

## Pre-declared sweep grid (single-axis, around the run-2 baseline)

| Axis | Values | Bound |
|---|---|---|
| `band_atr_mult` (σ units) | 2.5*, 2.75, 3.0, 3.25 | contract 2.5–3; 3.25 flagged as one step beyond, exploratory |
| `adx_max` | 15, 20* | stricter only (principle 5) |
| `time_stop_bars` | 15, 20* | contract 15–20 |
| `stop_atr_mult` | 0.75, 1.0* | tighter only (principle 3) |
| `rr_multiple` | 2.0*, 2.5, 3.0 | R:R ≥ 2 only (principle 2) |

(* = run-2 baseline.)

## Pre-set verdict rules

- Any cell with PF ≥ 1.2 AND n ≥ 40 → don't-give-up trigger; that axis
  earns a combined confirmation run.
- No such cell → **variant 1 closes**: "no deployable edge at
  literature-faithful settings on this window," with the standing caveat
  that this is an 8-month window, not the contract's 3-year frame, and
  the variant may be revisited when 3-year continuous data is assembled.
  Research proceeds to variant 2 (opening range fade).
- Sub-50 samples are directional evidence for iteration, never
  promotion — promotion still requires the full contract gate.
