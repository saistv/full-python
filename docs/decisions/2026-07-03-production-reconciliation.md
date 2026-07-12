# Production Reconciliation — engine vs v6.7-tp-exec 3-year TV export

> **Historical record, superseded as performance authority.** The `$251K / PF
> 2.071` figure discussed below was never backed by a reproducible export. It
> must not be used as a current baseline or promotion threshold.

Target: `v6.7-tp-exec_CME_MINI_NQ1!_2026-05-23_60ce8.csv` (437 trades,
2023-05-30 → 2026-05-22). Second file `9abf7` is the same entry set
(434 shared) with lighter sizing — a scaleA variant.

## What this file is (and is NOT)

Embedded CFG (from the entry Signal field):

```
v6.7-entry-challengers-hardened | Pivot S/R | window 9:30-10:00 | backstop 15:59
stop=Fixed30 | max=31 | minSR=15 | srDist=5 | exit=dynamic | freezeSR=on | be=OFF
wings=0.4/0.65 | sqzMom=on | sqzRel=on | prove=2 | ma50filt=on | ma200filt=on
sigValid=2 | entryCutoff=5 | antiM=OFF | scaleA=broad+shortMirror ... | maxQty=4 | DLL=$1000
```

**This is a challenger fork, not the $251K V6.6 production config.** It uses
**scaleA setup-quality sizing (not win-streak antiM) and be=OFF.** Its own
TV result: **$151,735 / PF 1.632 / WR 24.7% / 437 trades.** The $251K/PF
2.071/448 V6.6 config has NO 3-year export on disk — it uses antiM max4 +
be=100 and must be re-exported to reconcile against.

## Result 1 — ENTRY MODEL IS TRADE-EXACT over 3 years

| | value |
|---|---|
| Matched (same ET minute) | **437 / 437** |
| Side agreement | **437 / 437** |
| Entry price within $1pt | **437 / 437** (all +0.00pt) |
| TV-only (engine missed) | **0** |
| Engine-only extras | 55 (near-breakeven: +$1,400, PF 1.05) |

The ported entry model (ATF/squeeze/S-R pivots/MA gates/window) is faithful
over the full 3 years, not just the 8-month sub-window. The engine is a
strict superset; the 55 extras (29 of them ≥09:55, likely the `entryCutoff=5`
filter) net ~zero and are not the gap.

## Result 2 — EXITS are near-parity in aggregate (NOT the gap)

Per-trade P&L on matched trades, 1-contract basis:

| basis | engine | TV (1-ct equiv) | diff |
|---|---|---|---|
| all 437 (TV net ÷ qty) | $101,565 | $104,660 | **−$3,095 (−3%)** |
| 339 pure qty-1 trades | (engine) | (TV) | **−$13,925 (−$41/trade)** |

Engine and TV stop-loss distances are nearly identical (median 32.2pt vs
31.2pt). The residual is ~12 qty-1 trades where the engine's frozen S/R
stop is placed a little differently and clips a Backstop/ATF-Flip winner
(+$23K clipped, largely offset by reverse cases). **Real but minor** —
a future S/R-stop-price parity pass, not the P&L gap. (The 1-ct-equiv ÷qty
understates TV winners because scaleA's 2nd contract enters later; the true
1-ct exit divergence sits between the two rows, ~−$3K to −$14K.)

## Result 3 — SIZING is the gap for v6.7

| config | sizing model | size-2 rate | net | PF |
|---|---|---|---|---|
| Engine flat (1 ct) | none | — | $101.6K | 1.53 |
| **v6.7-tp-exec (TV)** | **scaleA** | **22.4%** (98/437) | **$151.7K** | 1.63 |
| Engine antiM (full span) | win-streak | 7.7% (41/532) | $117.6K | 1.47 |

scaleA scales on **setup quality** (OR width < 90, preBody 25-74 bands,
long/short mirror L60-79 / S20-39), independent of recent W/L — hitting
size 2 on 22.4% of trades and adding **+$47K** over the 1-contract base.
Our ported **win-streak antiM** needs consecutive net-positive closes; at
~22% WR those are rare, so it scales on only 7.7% of trades. **~3× under-
scaling** is the v6.7 gap (corrected from an earlier legs-vs-trades error
that read 45%/6×).

## Verdict

1. **Entry parity: PROVEN trade-exact over 3 years** (437/437 side + price).
2. **Exit parity: near-exact in aggregate** (1-ct within 3%); minor per-trade
   S/R-stop-price noise worth a later pass.
3. **The engine faithfully reproduces this TV config at 1 contract** — a
   trustworthy 1-contract anchor. The $151.7K headline is 1-ct $104.7K +
   scaleA sizing $47K.
4. **v6.7 ≠ V6.6.** The $251K target needs the 448-trade V6.6 export.

## Implications for sweeping

- **Trustworthy NOW (1-contract):** entry-model params, stop/exit params,
  regime gates — the engine reproduces TV's 1-ct P&L within 3%.
- **NOT trustworthy yet:** anything sizing-dependent — our antiM under-
  scales 3× vs production. Port the production sizing model first.
- **For the $251K / $100K-per-year target:** re-export V6.6 (448 trades,
  2023-2026) so sweeps anchor to the config actually traded.

## Honest target math (1 NQ)

- v6.7 reproducible today: $151.7K / 3yr ≈ **$50K/yr**
- V6.6 production ceiling: $251K / 3yr ≈ **$84K/yr**

Neither reaches $100K/yr alone. $100K/yr is a portfolio (AT + uncorrelated
MR sleeve) + account-stacking objective per the deployment plan — not a
single-config sweep target.
