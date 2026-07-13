# Exchange Calendar Correction — Research Authority Restored

**Decision:** the 2026-07-12 "Phase 0 holiday fix" is reverted and replaced. It
applied a **US cash-equity** holiday calendar to a **futures** market, deleted
trades TradingView had actually taken, and broke trade-level parity without
detection. This document records the correction, the restored authority numbers,
and which earlier tables are superseded.

## What was wrong

CME equity-index futures are not the cash equity market. On seven US holidays the
cash market is shut but **NQ trades an abbreviated 09:30–13:00 ET session** — the
strategy's entire 09:30–10:00 entry window is open, with real volume:

| Session | Calendar said | Exchange record (Databento GLBX) |
|---|---|---|
| MLK, Presidents, Memorial, Juneteenth, Independence, Labor, Thanksgiving | closed, no RTH | **210 RTH bars, 09:30–13:00 ET**, 4.0k–15.3k contracts in the entry window |
| Good Friday, Christmas, New Year | closed | **0 RTH bars** — genuinely closed |
| Day after Thanksgiving, Christmas Eve, July 3 | early close 13:00 | early close **13:15** (last bar 13:14) |

Three consequences, in ascending order of seriousness:

1. **A wrong early-close time.** Futures run 15 minutes past the cash close.
2. **An unregistered strategy filter shipped as a correctness fix.** Blocking the
   seven abbreviated sessions is a *trading decision*, not a data correction. Over
   five years it is worth **+$965** — an order of magnitude below the locked
   $10,000 materiality bar. Run through the Gate 1 promotion table it would be
   **REJECTED**. It nevertheless became the baseline every future candidate is
   measured against.
3. **Trade-level TradingView parity was broken and the evidence overwritten.**
   The three anchor trades it deleted were all matched 1:1 against real TV trades
   in the 106/106 reconciliation. Parity silently fell to **103/106**. Nothing
   caught it, because the only committed artifact linking the engine to
   TradingView — `tests/fixtures/golden_trades.json` — was regenerated from the
   engine's own new output. **A sim-vs-sim fixture cannot detect a
   sim-vs-TradingView regression.**

## The corrected calendar

`src/full_python/data/exchange_calendar.py` now returns a per-day close:

- **Full closure (`None`):** Good Friday, Christmas, New Year's Day, plus an
  explicit `AD_HOC_FULL_CLOSURES` list (currently 2025-01-09, the National Day of
  Mourning for President Carter — not derivable from any rule).
- **Abbreviated holiday session (13:00 ET):** MLK, Presidents, Memorial,
  Juneteenth (≥2022), Independence Day (observed), Labor Day, Thanksgiving.
- **Scheduled early close (13:15 ET):** day after Thanksgiving, Christmas Eve,
  day before Independence Day.
- **Regular (16:00 ET):** everything else.

Entries are permitted on every open session. Only the **backstop** moves: it is
now `min(15:59, close − 1)`, so an abbreviated session flattens at 12:59 and an
early close at 13:14 — deliberately, rather than relying on the session-boundary
fallback to close the position at whatever the last bar happened to be.

**The rules are pinned to the exchange, not to our reasoning.**
`tests/fixtures/cme_equity_rth_close.json` is derived from five years of Databento
GLBX NQ bars — for all **1,379 weekdays**, the last RTH minute observed — and
`tests/test_exchange_calendar.py` asserts the calendar reproduces every one.

Two observance rules came out of that data rather than intuition, and both
contradict the naive convention:

- **New Year's Day on a Saturday is not observed at all** (2021-12-31 traded a
  full regular session), while **Christmas on a Saturday is** observed on the
  preceding Friday (2021-12-24 was closed).
- **CME ran no holiday schedule for the first federal Juneteenth (2021).**

## Parity restored

| | Wrong calendar | **Corrected** | Pre-regression truth |
|---|---:|---:|---:|
| Anchor trades | 112 | **115** | 115 |
| Anchor net | $56,805.00 | **$55,875.00** | $55,875.00 |
| TradingView reconciliation | 103/106 (unmeasured) | **106/106, 0 missing, $0.00 entry delta** | 106/106 |

`golden_trades.json` is rebuilt from the reconciled anchor (115 trades).
`tests/test_tv_reconciliation.py` now guards the property that broke: an always-on
test that the abbreviated holiday sessions are still traded, plus an opt-in test
that performs the real reconciliation when the operator's TV export is available
(`FULL_PYTHON_TV_EXPORT`).

## Corrected five-year authority

Data 2021-03-16 → 2026-06-26. Costs: 0.75 pt/side, $10 NQ / $1 MNQ round trip.
**These supersede the tables in the Phase 0, Phase 1 and Phase 2 documents.**

| Metric | NQ (AM, $1,000 DLL) | MNQ (AM, $1,000 DLL) | MNQ pilot (flat 1, $150 DLL) |
|---|---:|---:|---:|
| Trades | 829 | 875 | 875 |
| Net P&L | **$159,160.00** | $25,649.50 | $12,296.50 |
| Profit factor | 1.412 | 1.519 | 1.316 |
| Win rate | 22.2% | 21.6% | 21.6% |
| Expectancy/trade | $191.99 | $29.31 | $14.05 |
| Observed max drawdown | -$19,775.00 | -$3,069.50 | -$2,156.50 |
| **Bootstrap maxDD p95 / p99** | **-$43,080 / -$55,610** | -$5,707 / -$7,476 | -$4,521 / -$5,869 |
| Annualized net, 95% | $8,262 – $51,025 | $1,100 – $9,441 | $447 – $4,118 |
| Positive forward folds | 5/7 | 4/7 | 4/7 |

Forward folds (NQ): 2023 H1 -$885 · 2023 H2 -$8,200 · 2024 H1 +$2,385 ·
2024 H2 +$44,980 · 2025 H1 +$27,575 · 2025 H2 +$1,735 · 2026 H1 +$50,055.

Every qualitative conclusion of Phase 1 and Phase 2 survives the correction:

- **Capital planning still uses bootstrap p95/p99, not observed drawdown.** For
  1 NQ that is **≈ -$43K**, not the -$19.8K observed.
- **2023 remains a full-year loss regime.** The system will look broken for
  months while behaving within historical precedent.
- **The MNQ pilot still fails its own registered gate** — 23.7% probability of
  touching the -$500 budget over 30 sessions (bar: ≤5%), p95 drawdown -$908
  (bar: within $500). The rejection in
  `docs/decisions/2026-07-13-mnq-pilot-sizing.md` **stands**; only the input
  numbers move.

## Component ablation, re-run on the corrected baseline

| Scenario | Trades | Net P&L | PF | Max DD | Loss streak | Folds |
|---|---:|---:|---:|---:|---:|---:|
| Reference | 829 | $159,160 | 1.412 | -$19,775 | 22 | 5/7 |
| No squeeze momentum | 846 | $161,760 | 1.411 | -$16,700 | 23 | 5/7 |
| No squeeze release | 862 | $152,255 | 1.381 | -$18,940 | 20 | 5/7 |
| No wings | 1,099 | $137,560 | 1.282 | -$31,650 | 22 | 4/7 |
| No prove-it hold | 1,009 | $116,295 | 1.269 | -$35,665 | **37** | 5/7 |

Conclusions unchanged: **wings** (-$21,600, drawdown 60% worse) and the
**prove-it hold** (-$42,865, loss streak 22→37) are load-bearing, not decoration;
squeeze release adds smaller but real selectivity; removing squeeze momentum gains
**+$2,600**, still below the $10,000 materiality bar, so it remains
hypothesis-generating only and **is not promoted**.

## Standing rule

The exchange calendar is **data**, not strategy. A decision to skip a session the
market is open for is a **filter**, and filters go through Gate 1 like anything
else. If skipping the abbreviated holiday sessions is still wanted on
thin-liquidity grounds, register it as a candidate — on the evidence it fails
materiality, so the honest default is to trade them.

## Superseded

- `2026-07-12-phase0-correctness-remediation.md` — the holiday/early-close rows of
  its Failure Matrix (J8) and its entire "Corrected Evidence" table.
- `2026-07-12-phase1-evidence-migration.md` — the five-year authority table.
- `2026-07-12-phase2-baseline-walk-forward.md` — fold table.
- `2026-07-13-phase2-component-ablation.md` — results table (conclusions stand).
- `2026-07-13-mnq-pilot-sizing.md` — input numbers (the **decision stands**).
