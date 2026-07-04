# Parity Delta Report — Decomposed, Not Aggregated

**EXECUTED** against the frozen anchor (`docs/decisions/2026-07-04-python-baseline-anchor.md`) and the real TV AM/DLL export `AT-RSRCH_CME_MINI_NQ1!_2026-07-03_9e40f.csv`, trimmed to entries before 2026-06-27 to match the anchor's window. This decomposition is what caught a real bug before the anchor was trusted: the first freeze attempt (before `rth_open_extra_entry_slippage_points` was corrected to `0.0`) had a **100% aggregate match rate with 84 of 106 trades silently off by exactly $1.00 on entry price** — the per-trade `entry_price_exact_count` dimension caught it; the aggregate `match_rate` alone would not have.

## Purpose

`full_python.reconcile.reconcile()` already produces an aggregate
match-rate and per-trade deltas. That aggregate can hide a structural
mismatch: e.g. every exit price could match to the penny while every
exit *reason* label disagrees, and an aggregate "match rate" would never
surface it. `full_python.parity_report.build_parity_delta_report()`
decomposes the aggregate into per-dimension exact-match counts (entry
timestamp, entry price, exit timestamp, exit reason, exit price) plus the
`largest_n` (default 20) trades by absolute P&L delta, so each dimension
must be verified — and the largest deltas reviewed by hand — rather than
accepted because the whole looks close.

## Required-checks table

| Dimension | Metric | Value |
|---|---|---|
| Trade count | `trade_count_exact` (tv == sim == matched) | **False** (106 == 106, but sim has 115 — see note) |
| Trade count | `tv_trade_count` | 106 |
| Trade count | `sim_trade_count` | 115 |
| Trade count | `matched_count` | 106 |
| Entry timestamp | `entry_timestamp_exact_count` (of matched) | **106 / 106 (100%)** |
| Entry price | `entry_price_exact_count` (of matched) | **106 / 106 (100%)** |
| Exit timestamp | `exit_timestamp_exact_count` (of matched) | 93 / 106 (87.7%) |
| Exit reason | `exit_reason_exact_count` (raw-string exact match after lowercase/underscore normalization only — no semantic mapping) | 13 / 106 (12.3%) |
| Exit price | `max_abs_exit_price_delta` | $8.00 (1 trade); all others ≤ $1.00 |
| P&L | count of matched pairs with non-null `net_pnl_delta` | 106 / 106 (100% — all matched pairs have both a TV and sim P&L) |

**`trade_count_exact = False` is expected, not a failure**: `sim_trade_count` (115) exceeds `tv_trade_count` (106) by exactly 9 — the documented out-of-TV-history extras (sim trades before 2025-10-28, before TV's 1-minute chart history starts). Every TV trade in the window has a sim match; there is no unexplained gap.

**`exit_timestamp_exact_count` (87.7%) and `exit_reason_exact_count` (12.3%) are both expected, not parity failures**, per the two known, pre-documented divergence classes below — not "unexplained" or "waved off because the aggregate looks acceptable." The low exit-reason count is a label-vocabulary artifact: TV's exit signal strings ("Stop Loss", "Hard Backstop") and the sim's `exit_reason` strings ("stop", "session_flatten") come from two different systems' vocabularies and only coincidentally normalize to equal strings for `ATF Flip`/`atf_flip`; this module deliberately does not build a semantic mapping between them (see `parity_report.py`'s docstring) — a future consumer needing "same underlying exit cause" should apply an explicit, reviewed mapping, not rely on string equality.

Entry timestamp and entry price are both **exact on every single matched trade** — this is the strongest possible parity result on the dimension that matters most for signal-timing correctness, and it required the `rth_open_extra_entry_slippage_points` fix described in the anchor doc to achieve (the first attempt was 100% on entry price only by coincidence of aggregate match rate, while actually wrong on 84/106 trades individually).

## Largest P&L delta review (manual, by hand)

All 8 matched pairs with a nonzero `net_pnl_delta` (of 106 total; the remaining 98 have `net_pnl_delta == 0.0` exactly) — every one falls into a documented divergence class from the historical reconciliation, no unexplained deltas:

| # | tv_trade_number | side | tv_net_pnl | sim_net_pnl | net_pnl_delta | Explanation |
|---|---|---|---|---|---|---|
| 1 | 13 | short | -$345.00 | -$185.00 | $160.00 | **Half-day close** (documented class). Sim's `exit_reason=session_end` vs TV's "Stop Loss" — TV's chart had no 15:59 ET bar that session (half trading day), so TV's own backstop logic diverged from a normal day; sim closed on the session's last available bar per its own session-boundary rule. Same trade as the flat-gate reconciliation's documented "TV#13, 8pt" case (`docs/decisions/2026-07-03-first-tv-reconciliation.md`) — exact same 8-point exit-price delta reproduced here in the AM/DLL export. |
| 2 | 47 | short | $10,145.00 | $10,165.00 | $20.00 | **Flatten fill timing** (documented class). TV's "Hard Backstop" fills at the *next* bar's open; sim's `session_flatten` fills at *that* bar's close. 1-minute exit-time delta, $1.00 exit-price delta. |
| 3 | 106 | short | $6,605.00 | $6,590.00 | -$15.00 | Same flatten-fill-timing class as #2 ($0.75 exit-price delta, 1-minute exit-time delta). |
| 4 | 3 | short | $3,270.00 | $3,280.00 | $10.00 | Same flatten-fill-timing class ($0.50 exit-price delta, 1-minute exit-time delta). |
| 5 | 65 | short | $6,125.00 | $6,135.00 | $10.00 | Same flatten-fill-timing class ($0.50 exit-price delta, 1-minute exit-time delta). |
| 6 | 5 | short | $5,920.00 | $5,910.00 | -$10.00 | Same flatten-fill-timing class ($0.50 exit-price delta, 1-minute exit-time delta). |
| 7 | 28 | short | $1,135.00 | $1,140.00 | $5.00 | Same flatten-fill-timing class ($0.25 exit-price delta, 1-minute exit-time delta). |
| 8 | 93 | short | $1,825.00 | $1,830.00 | $5.00 | Same flatten-fill-timing class ($0.25 exit-price delta, 1-minute exit-time delta). |

**7 of the 8 nonzero deltas are the single documented flatten-fill-timing class** (TV fills the 15:59 backstop at the next bar's open, sim fills at that bar's close) — exactly matching the "7 exits, ≤1.0pt" class in `docs/decisions/2026-07-03-first-tv-reconciliation.md`. **The 8th is the single documented half-day-close class** (TV#13, exactly reproducing the historical 8-point delta). No new divergence classes appeared. All other 98 matched trades — including every trade with a nonzero *entry* price in the pre-fix run — now have `net_pnl_delta == 0.0` exactly.

## Conclusion

Entry-side parity (timestamp + price) is exact on 106/106 matched trades. The only P&L deltas are two previously-documented, previously-explained exit-mechanics classes (flatten-fill-timing and half-day-close), with no new discrepancies. This report is the direct evidence that the Python Baseline Anchor's trade population is behaviorally faithful to the TV production config, not merely close in aggregate.

## How this report was produced

```bash
cd "/Users/sais/Documents/New Beginning/full-python"
PYTHONPATH=src python3 -c "
import json
from full_python.parity_report import build_parity_delta_report
from full_python.reconcile import load_sim_trades, load_tv_trades, reconcile
import datetime
from zoneinfo import ZoneInfo

tv = load_tv_trades('/Users/sais/Downloads/AT-RSRCH_CME_MINI_NQ1!_2026-07-03_9e40f.csv')
cutoff = datetime.datetime(2026, 6, 27, tzinfo=ZoneInfo('America/New_York'))
tv = [t for t in tv if t.entry_time < cutoff]
sim = load_sim_trades('runs/baseline-anchor/trades.csv')
report = reconcile(tv, sim, tolerance_minutes=3.0)
parity = build_parity_delta_report(report)
print(json.dumps(parity.to_dict(), indent=2, default=str))
"
```

Inputs: `AT-RSRCH_CME_MINI_NQ1!_2026-07-03_9e40f.csv` (the `am=1-4|dll=$1000` TV export, trimmed to entries before 2026-06-27 to match the anchor's window) and `runs/baseline-anchor/trades.csv` (from `scripts/freeze_baseline_anchor.py`, after the `rth_open_extra_entry_slippage_points` fix documented in the anchor doc).
