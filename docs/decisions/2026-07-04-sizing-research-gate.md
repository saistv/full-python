# Capital Allocation / Sizing Research Gate — 2026-07-04 (data-limited)

## Scope caveat (binding, do not drop in any future reference to this doc)

This gate runs on the frozen 9-month Python Baseline Anchor
(docs/decisions/2026-07-04-python-baseline-anchor.md), n=115 trades. The
full spec's Gate 1 promotion table requires a train/holdout split
(2023-01->2025-06 / 2025-07->2026-06) and top-1/2/3-trade-removal +
year-by-year robustness checks. **None of those are statistically
meaningful at n=115 over 9 months.** This document reports mechanically
comparable candidates only; it is not a promotion decision, and no
candidate here should be treated as cleared for live deployment on this
evidence alone. Re-run this exact comparison once a multi-year dataset
exists.

**Executed** against the real 9-month CSV assembled for the anchor
(`runs/baseline-anchor/nq1_2025-10-01_2026-06-26.csv`).

## Candidate 1: 1 NQ vs 1 MNQ (point-value/commission only)

- 1 NQ: point_value=20, commission_rt=10, trade_count=**115**, net P&L=**$55,875.00**, max drawdown=**-$9,150.00**
- 1 MNQ: point_value=2, commission_rt=1, trade_count=**129**, net P&L=**$5,259.50**, max drawdown=**-$1,049.00**
- Net P&L ratio (NQ/MNQ): **10.62x**

**Real finding — the "same trades, different multiplier" assumption in this candidate's original framing was wrong for `adaptive_trend_am` specifically.** Trade count is *not* identical between the two runs (115 vs 129, a 14-trade / 12% difference) — a genuine discovery, not measurement noise or a bug, confirmed by the test's initial failure (`assert nq_report[...]["trade_count"] == mnq_report[...]["trade_count"]` failed with `115 == 129`) before the assumption was corrected.

**Why the two runs have different trade counts.** `adaptive_trend_am`'s daily-loss-limit ($1,000) and its projected-risk sizing guard are both **dollar-denominated**, not point-denominated. The projected-risk guard caps entry quantity to `floor((session_pnl + $1,000 − ε) / (stop_distance_points × point_value))` — at NQ's `point_value=20`, the same point-distance stop consumes 10x more of the $1,000 budget per contract than at MNQ's `point_value=2`. So a stop distance that fully consumes the daily budget (and blocks the next entry) at NQ scale leaves 90% of the budget unused at MNQ scale, letting MNQ take entries that NQ's guard blocks. The 14 extra MNQ trades are exactly the ones the guard vetoes at NQ scale (`dll_projected_risk` rejections in the NQ run's event ledger that don't appear in MNQ's). This means **sizing (contract choice) and the AM/DLL guard are not independent** for this strategy — a sizing decision changes which trades the risk layer even allows, not just how large the resulting P&L is. This is exactly the kind of interaction the plan's Capital Allocation Gate exists to surface, separated from pure edge research.

- Ratio is 10.62x, not exactly 10x: partly the standard commission-as-fraction-of-P&L effect (commission is a smaller drag on NQ's larger per-trade P&L), and partly the different trade populations above (MNQ's 14 extra trades are ones the NQ guard blocked, so they're not simply "the same trades scaled down" — they contribute their own P&L to the MNQ total that has no NQ counterpart at all).

## Candidates not run in this pass (require more history or live infrastructure)

- Volatility-scaled sizing, drawdown-based de-risking: need enough trades per
  volatility/drawdown bucket to be meaningful; 9 months does not provide it.
- Anti-martingale (already built, already in the frozen anchor via
  `production_am_config()` -- not a new candidate to test, it's the baseline).
- Prop-firm consistency-cap compliance, daily-stop interaction,
  account-size-specific deployment: these are policy constraints to check the
  existing frozen anchor against, not sizing models to sweep; do as a
  follow-up pass reading `docs/decisions/2026-07-04-python-baseline-anchor.md`'s
  daily P&L series against each target account's consistency-cap rule.

## Conclusion

1 NQ vs 1 MNQ turned out **not** to be the purely mechanical comparison
the plan assumed — the AM/DLL guard's dollar-denomination makes contract
choice and risk-gating interact. NQ nets more in absolute dollars
(as expected — larger point value) but also takes *fewer* trades because
its larger effective per-point dollar risk trips the daily-loss
projected-risk guard more often; MNQ's smaller per-point risk lets 14
more trades through. Neither "NQ is strictly better" nor "MNQ is strictly
better" is supported by this single data point — NQ has ~10.6x the P&L
of MNQ on ~89% of the trade count, and both numbers came from the same
9-month, 115/129-trade sample that the scope caveat above already flags
as too small for a promotion decision. The candidate needs the same
statistical machinery (Gate 1's Welch-t / holdout / robustness table) as
any edge-research candidate before either sizing choice is treated as
proven — it is not exempt just because it looked mechanical on paper.

Everything else on the spec's candidate list (volatility-scaled sizing,
drawdown de-risking, prop-firm consistency-cap compliance) still needs
either more history or a running paper/live system and remains deferred,
not rejected.
