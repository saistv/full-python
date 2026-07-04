# Capital Allocation / Sizing Research Gate — 2026-07-04 (data-limited)

## Scope caveat (binding, do not drop in any future reference to this doc)

This gate runs on the frozen 9-month Python Baseline Anchor
(docs/decisions/2026-07-04-python-baseline-anchor.md), n=<paste trade
count>. The full spec's Gate 1 promotion table requires a train/holdout
split (2023-01->2025-06 / 2025-07->2026-06) and top-1/2/3-trade-removal
+ year-by-year robustness checks. **None of those are statistically
meaningful at n=<paste trade count> over 9 months.** This document
reports mechanically comparable candidates only; it is not a promotion
decision, and no candidate here should be treated as cleared for live
deployment on this evidence alone. Re-run this exact comparison once a
multi-year dataset exists.

**Not run in this environment.** The comparison test
(`tests/test_sizing_candidates.py::test_1nq_vs_1mnq_sizing_comparison_on_the_frozen_window`)
requires `FULL_PYTHON_BASELINE_DATA` to point at the operator's real
9-month NQ CSV, which does not exist in this sandboxed working copy. The
test collects and skips cleanly here (verified: `1 skipped`). Every
`<paste ...>` placeholder below is left unfilled rather than fabricated;
this doc must be updated with the real run's numbers before it is cited
as evidence for anything.

## Candidate 1: 1 NQ vs 1 MNQ (same signal core, point-value/commission only)

- 1 NQ: point_value=20, commission_rt=10, net P&L = <paste>
- 1 MNQ: point_value=2, commission_rt=1, net P&L = <paste>
- Trade count identical (same signal timing, confirmed): <paste count> both sides
- Ratio: <paste> (expected ~10x; commission is a smaller fraction of NQ's larger
  per-trade P&L, so the ratio is not exactly 10x)

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

1 NQ vs 1 MNQ is the only candidate in this list that is purely mechanical
(same trades, different multiplier) and therefore safe to compare even at
n=<paste trade count>. Everything else on the spec's candidate list needs
either more history or a running paper/live system and is deferred, not
rejected.

This comparison itself has not been executed in this environment (see
scope caveat above) — the operator must re-run Step 2 of the Task 8 brief
with `FULL_PYTHON_BASELINE_DATA` set to the real 9-month CSV and fill in
every placeholder above before this doc can support any sizing decision.
