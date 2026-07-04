# Parity Delta Report — Decomposed, Not Aggregated

> **NOT YET EXECUTED.** This report requires two inputs that do not exist
> in this sandboxed worktree: (1) the operator's local TradingView "List
> of trades" export CSV, and (2) `runs/baseline-anchor/trades.csv`, which
> is itself produced by `scripts/freeze_baseline_anchor.py` and was
> scope-cut in Task 4 (see
> `docs/decisions/2026-07-04-python-baseline-anchor.md` — placeholders
> only, not yet run against real data). Every numeric placeholder below
> reads `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>`
> until both inputs exist and `build_parity_delta_report()` has actually
> been run against them. Do not fill in a number without running the real
> command in Step 10 of the Task 5 brief first, and do not treat a
> plausible-looking number as evidence.

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
| Trade count | `trade_count_exact` (tv == sim == matched) | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| Trade count | `tv_trade_count` | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| Trade count | `sim_trade_count` | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| Trade count | `matched_count` | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| Entry timestamp | `entry_timestamp_exact_count` (of matched) | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| Entry price | `entry_price_exact_count` (of matched) | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| Exit timestamp | `exit_timestamp_exact_count` (of matched) | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| Exit reason | `exit_reason_exact_count` (raw-string exact match after lowercase/underscore normalization only — no semantic mapping) | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| Exit price | `max_abs_exit_price_delta` | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| P&L | count of matched pairs with non-null `net_pnl_delta` | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |

Any dimension short of 100% of `matched_count` (other than P&L, which is
expected to differ by commission/slippage modeling and is reviewed
separately below) is a parity failure for that dimension and must be
explained per-trade, not waved off because the aggregate match rate looks
acceptable.

## Largest-20 P&L delta review (manual, by hand — not automated)

The spec requires a human review of each entry in
`parity.largest_pnl_deltas`, not another automated pass. Per entry:
tv_trade_number, side, tv_net_pnl, sim_net_pnl, net_pnl_delta, and an
explanation (fill-timing difference, commission/slippage model
difference, intrabar ambiguity, roll-boundary effect, or "unexplained —
investigate further").

| # | tv_trade_number | side | tv_net_pnl | sim_net_pnl | net_pnl_delta | Explanation |
|---|---|---|---|---|---|---|
| 1 | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| 2 | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending real data — see docs/decisions/2026-07-04-python-baseline-anchor.md>` |
| ... | | | | | | (up to 20, or all matched pairs if fewer than 20) |

## How to produce this report for real

```bash
cd "/Users/sais/Documents/New Beginning/full-python"
PYTHONPATH=src python3 -c "
import json
from full_python.parity_report import build_parity_delta_report
from full_python.reconcile import load_sim_trades, load_tv_trades, reconcile

tv = load_tv_trades('/path/to/operator/AT-RSRCH_..._TV_export.csv')
sim = load_sim_trades('runs/baseline-anchor/trades.csv')
report = reconcile(tv, sim, tolerance_minutes=3.0)
parity = build_parity_delta_report(report)
print(json.dumps(parity.to_dict(), indent=2, default=str))
"
```

Prerequisites, neither of which exists in this worktree yet:

1. The operator's local TradingView "List of trades" CSV export for the
   frozen anchor window (2025-10-01 -> 2026-06-26 per
   `docs/decisions/2026-07-04-python-baseline-anchor.md`).
2. `runs/baseline-anchor/trades.csv`, produced by running
   `scripts/freeze_baseline_anchor.py` against the real 9-month dataset
   (Task 4 — also scope-cut, see that task's decision doc).

Once both exist, run the command above, fill in every placeholder in
this document with the real printed numbers, and complete the by-hand
largest-20 review before this report can be cited as evidence of parity.
