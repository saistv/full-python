# M2b — Anti-Martingale + Daily Loss Limit, Reconciled

## What was ported

From `strategy_RESEARCH.pine`, on top of the flat parity core:

- **Anti-martingale sizing** (strategy layer): win streak counted on
  commission-inclusive per-trade net P&L; strictly positive extends the
  streak, any non-win (including scratches) resets it — Pine's "Any Non-Win"
  mode, the production setting. Entry quantity = min(1 + streak, 4). The
  streak persists across sessions.
- **Projected-risk DLL guard** (strategy layer): at signal time, quantity is
  capped to `floor((session_pnl + $1,000 − ε) / (stop_distance × $20))`;
  zero blocks the entry (`dll_projected_risk` rejection in the ledger).
- **Equity-based daily loss limit** (engine layer): session P&L = realized
  net since session start + gross unrealized at bar close, matching
  `strategy.equity` semantics. On breach: `daily_limit_hit` event, stop
  cancelled, flatten fills at the next bar's open (reason `daily_limit`),
  entries vetoed for the rest of the session; halt lifts at the session
  boundary. The strategy receives session P&L and the halt flag per bar via
  the `on_bar_context` hook.

`--strategy adaptive_trend_am` runs the production stack; `adaptive_trend`
remains the reconciled flat core.

## Reconciliation vs the AM/DLL TradingView export

Same continuous dataset as the flat gate; export
`AT-RSRCH_..._9e40f.csv` (`am=1-4|dll=$1000` per the embedded CFG).

**106/106 trades matched, zero quantity mismatches, zero missing — first
run, no calibration.** Quantity pairs: 103 × (1,1), 1 × (2,2), 2 × (3,3) —
the anti-martingale streak sequence reproduced exactly (TV #6, #38, #52).
The 9 sim extras predate TV's 1m history (out of scope); the single nonzero
price delta remains the June 15 roll-basis trade.

## Notes

- The TV export contains **zero** "Daily Loss Limit Hit" exits: over these
  8 months the DLL never flattened a live position. Its entire economic
  effect came from the projected-risk guard blocking follow-up entries
  after a ~$650 first loss (the sim ledger shows the matching
  `dll_projected_risk` rejections). The engine's flatten path is therefore
  verified by unit test, not yet by a live occurrence.
- 8-month sim (1 NQ base + AM, Oct 1 → Jun 26): net ≈ $55.9K vs $42.5K
  flat, max DD ≈ $9.2K (smaller than flat's $10.5K — the guard truncates
  the second loss of a bad morning), daily Sharpe ≈ 2.4.

## Status

M2 and M2b authority gates are both passed. The full production
configuration — signal core, dynamic S/R stops, AM sizing, DLL — now runs
in Python with trade-for-trade, contract-for-contract TradingView parity.
