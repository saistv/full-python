# Prior-Session-Volatility Entry Gate — Design

## Context

Gate 1 Phase 2 diagnosis (`docs/decisions/2026-07-05-gate1-phase2-diagnosis.md`,
corrected 2026-07-05 after a methodology error was caught by this
feature's own final review — see that doc's correction note) found a
statistically significant effect on the train window (2023-01-01 →
2025-06-30, 378 trades): sessions following a high-prior-day
realized-volatility session net **-$22,610** across 128 trades (10.2%
win rate), Welch `t = -2.762` against the rest of the population — past
the Phase 0 materiality bar's `|t| >= 2.0` threshold — and the effect
survives removing the 3 largest winning trades in that bucket (still
`-$46,885`, i.e. more negative, not less).

Per explicit user direction, this is not being dismissed as
"measurement only, don't act on it" just because it borders the
previously-closed "regime filters exhausted" finding — that prior
conclusion came from manual TradingView testing of a different signal
(AER-based), and the user wants Python's speed to re-examine, not
inherit, TV-era conclusions as permanent (see `feedback_python_reopens_
closed_axes` memory). This spec designs the actual candidate: a
config-gated, session-level entry veto, built as a real feature from the
start, evaluated through the full Gate 1 promotion table before any
production decision is made.

## Goal

Add an optional, off-by-default entry gate to `AdaptiveTrendStrategy`
that blocks all entries for a trading session when the *prior* completed
session's realized volatility exceeds a fixed, train-calibrated
threshold. Ship it as a real config option so a Gate 1 promotion pass
requires no rework — not a throwaway validation script.

## Architecture

Self-contained inside `strategy/adaptive_trend_config.py` and
`strategy/adaptive_trend.py`. No changes to `regime.py`, `simulation/
engine.py`, `replay.py`, or the `risk/` package extracted in the prior
migration work. The strategy already tracks its own internal
session-scoped state independently (win streak, cooldown counters, DLL
projected-risk budget) — this follows the same pattern rather than
introducing a new engine↔strategy interface.

### Config additions (`AdaptiveTrendConfig`)

```python
enable_prior_vol_gate: bool = False
prior_vol_high_threshold: float = 0.0004638315483775433  # train-calibrated,
    # see docs/decisions/2026-07-05-gate1-phase2-diagnosis.md; the high
    # tercile boundary of prior_realized_vol computed via
    # full_python.regime._tercile_bounds over ONLY the 2023-01-01 ->
    # 2025-06-30 train window (642 sessions with >=30 prior RTH closes).
    # Fixed at this value deliberately -- not recomputed dynamically --
    # to avoid lookahead into holdout/live data. Re-derive only if the
    # train window itself is redefined.
```

Both default to values that reproduce today's behavior exactly:
`enable_prior_vol_gate=False` means every other field on this config,
including the threshold, is inert.

### Strategy state (`AdaptiveTrendStrategy`)

Two new private fields, mirroring the existing style of internal
counters already on the class (e.g. `self._win_streak`,
`self._bars_since_last_entry`):

- `self._current_session_rth_closes: list[float]` — appended once per
  bar where `session.is_rth` is true; cleared at each session-date
  change (same session-change detection the strategy already performs
  for its cooldown counters).
- `self._prior_session_realized_vol: Optional[float]` — set once per
  session boundary, from the *just-completed* session's closes, and used
  to gate the *new* session's entries. `None` until the first session
  boundary with enough data has been observed (cold-start / warmup
  case), which behaves identically to "gate does not block" — no
  separate warmup flag needed.

At each session-date change, before the closes list is cleared for the
new session:

```python
if len(self._current_session_rth_closes) >= 30:
    returns = [
        math.log(c1 / c0)
        for c0, c1 in zip(
            self._current_session_rth_closes, self._current_session_rth_closes[1:]
        )
    ]
    mean = sum(returns) / len(returns)
    self._prior_session_realized_vol = math.sqrt(
        sum((r - mean) ** 2 for r in returns) / len(returns)
    )
# else: leave self._prior_session_realized_vol at its previous value
# (None on cold start, or the last successfully-computed value if a
# short session is ever skipped) -- a short/holiday session should not
# silently reset the gate to "unknown -> don't block" if the strategy
# has real recent volatility information; it also should never fabricate
# a value from too little data.
```

This is the identical formula to `full_python.regime.compute_
session_features`'s `prior_realized_vol` (stdev of log returns over the
prior RTH session's minute closes, `>=30` observations required) —
deliberately duplicated (not imported) per the approved architecture
choice, to keep the strategy free of a `regime.py` dependency. A unit
test asserts the two implementations agree on identical input data, so
the duplication can't silently drift.

### Gating check

Wherever the strategy currently evaluates whether to emit an entry
`OrderIntent` (the same place existing checks like cooldowns and the
DLL projected-risk guard are applied — exact insertion point is an
implementation-plan decision, not a design-level one), add:

```python
if config.enable_prior_vol_gate and self._prior_session_realized_vol is not None:
    if self._prior_session_realized_vol > config.prior_vol_high_threshold:
        # reject with reason "prior_vol_gate", same path as other
        # strategy-level rejections (dll_projected_risk, sr_not_confirmed)
        ...
```

This produces a `SignalDecision.rejected(..., reason="prior_vol_gate")`
in the same shape as existing rejections, so it shows up in the event
ledger and any rejection-count reporting without new plumbing.

## Testing

1. **Vol-calc parity**: unit test with synthetic bars asserting the
   strategy's internal calc matches an independently-computed reference
   (`statistics.pstdev` over the same log returns, not `regime.py` —
   deliberately a different code path so the check isn't just the
   implementation re-run against itself). This pins the strategy's
   formula to the correct math; it does NOT detect a future edit to
   `regime.py`'s own formula diverging from this one, since the two are
   never compared to each other directly. If `regime.py`'s formula ever
   changes, re-verify this strategy's copy by hand against the new
   formula — there is no automated cross-check between the two modules.
2. **Gate blocks when triggered**: synthetic multi-session bar sequence
   where session N's closes produce a realized vol above the fixed
   threshold; assert no entry fires on session N+1 with the gate
   enabled, and DOES fire with the gate disabled (same bars, same
   config otherwise).
3. **Gate never fires on cold start / insufficient data**: fewer than 30
   RTH closes in the prior session → no block, regardless of the flag.
4. **Regression safety**: `enable_prior_vol_gate=False` (the default)
   produces byte-identical output to today's strategy on the existing
   golden-trade fixture (`tests/fixtures/golden_trades.json`) — this is
   the test that proves the feature is truly inert until switched on.
5. **Full-backtest comparison**: run `production_am_config()` with the
   gate on vs. off over the train window (`runs/multi-year/nq1_2021-03-16_
   2026-06-26.csv`, sliced to train dates) and diff the resulting trade
   populations — this is the actual Gate 1 candidate evaluation, done as
   a follow-up analysis step after the feature lands, not part of this
   spec's test suite.

## Evaluation path (after this feature is built)

Not part of this implementation — recorded here so the next step is
clear. Once the gate exists and passes its own tests:

1. Run `adaptive_trend_am` with `enable_prior_vol_gate=True` over the
   **train** window only.
2. Score against every row of the Phase 0 promotion table
   (`docs/decisions/2026-07-05-gate1-phase0-protocol.md`): materiality
   bar, expectancy improvement, drawdown, top-1/2/3 outlier survival,
   year-by-year robustness, long/short symmetry, slippage sensitivity.
3. If and only if every row clears, run once on **holdout**
   (2025-07-01 → 2026-06-26) — same-sign result required, no re-running
   or re-parameterizing to chase a pass.
4. Record the outcome (promoted or rejected, with the specific numbers)
   in a new decision doc, regardless of which way it goes — a rejected
   candidate is still a real, documented finding per this migration's
   established pattern (see the sizing-gate and parity-report docs, both
   of which recorded real findings that weren't simple wins).

## Explicitly out of scope for this spec

- Running the actual train/holdout evaluation (step 4 above) — that's
  analysis work using the feature this spec builds, not part of this
  implementation.
- Any other regime axis (`adx`, `variance_ratio`, `gap`,
  `overnight_range`) — Phase 2 diagnosis found none of those
  significant; only `prior_vol` cleared the bar.
- Dynamic/rolling threshold recalibration — explicitly rejected in favor
  of the fixed, train-derived value (see Architecture above).
- Any change to `regime.py`, `simulation/engine.py`, or the `risk/`
  package.
