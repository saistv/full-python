# 1-Contract Sensitivity Sweep — flat AT engine, 3-year data

> **Historical record.** References below to `$275K / PF 2.071` predate the
> Python evidence migration and are not current promotion authority.

Trustworthy per the production reconciliation (engine reproduces TV 1-ct
P&L within 3%). Baseline: flat `adaptive_trend`, 566 trades, +$93,920,
PF 1.368, DD −$14,995. Single-axis; interaction effects not captured
(Standard 15).

## Result — 4 of 5 axes confirm locked optima (robustness win)

| axis | baseline | neighbors | verdict |
|---|---|---|---|
| `max_stop_distance` | 31 | 25:−21.0K  28:−20.7K  34:−4.7K  37:−10.8K | **31 optimal, sharp** ✓ |
| `signal_valid_bars` | 2 | 1:−13.6K  3:−17.2K | **2 optimal, sharp** ✓ |
| `atf_sensitivity` | 4.5 | 4.0:−12.1K  5.0:−16.2K | **4.5 optimal** ✓ (matches locked ATF) |
| `sr_stop_buffer` | 5 | 3:+1.2K  7:+2.7K | flat; mild wider-is-better hint |
| `sr_min_stop_distance` | 15 | 10:−6.7K  12:−5.8K  **18:+6.0K  20:+5.0K** | **only lead** — see below |

The 3-year data independently confirms `max=31`, `sigValid=2`,
`atf=4.5` at sharp peaks → the locked config is not overfit to a shorter
window. Strong robustness evidence.

## The one lead: minSR 15 → 18 — NOT actionable

Audit A/B (minSR=18 vs 15), per-trade P&L:

| metric | minSR=15 | minSR=18 |
|---|---|---|
| net | +$93,920 | +$99,955 (**+$6,035**) |
| PF | 1.368 | 1.391 |
| WR | 21.0% | 21.5% |
| median | −$570 | −$570 (unchanged) |
| DD | −$14,995 | −$14,580 (better) |
| **Welch t (vs baseline)** | — | **+0.11** |

- **Not significant:** Welch t = +0.11 ≪ 2.0. The populations are
  statistically indistinguishable.
- **Not outlier-driven:** top-3 winners identical in both (Standard 4
  passes — but the gain is broad and tiny, not a real edge).
- **Coherent mechanism:** wider stop → fewer premature stop-outs, matching
  the reconciliation's "S/R stop slightly too tight on ~12 trades"
  residual. `sr_stop_buffer=7` points the same direction.
- **Verdict:** a single-TV-backtest hypothesis at most. Standard 10 —
  stop changes are TV-only (Python exit approximations were $63K wrong on
  Quick Kill). NOT a Python-authority change. Nowhere near $275K/PF 2.071.

## Bottom line

The entry/stop parameter space is essentially exhausted — the config is
well-tuned and robust on 3 years. **P&L upside is not in these knobs.** It
is in (1) the sizing model (scaleA scales 22.4% vs our 7.7% → +$47K lever,
not sweepable until anchored) and (2) portfolio + account stacking (the
real $100K/yr path; V6.6 ceiling ≈ $84K/yr on 1 NQ). Recommend proceeding
to the V6.6 export + sizing anchor before any further sweeping.
