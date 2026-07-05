# S/R Interaction Sweep (sr_min_stop_distance × sr_stop_buffer) — Design

## Context

Last open axis of Gate 1 Phase 4
(`docs/decisions/2026-07-05-gate1-phase0-protocol.md`). Phase 0 names
this pair explicitly as the one axis interaction to check jointly
(Standard 15: single-dimension sweeps miss interaction effects). The
MA-length sweep (`docs/decisions/2026-07-05-ma-length-sweep-closed.md`)
closed with no qualifier; this sweep reuses the identical harness
(`src/full_python/research/sweep.py`) and protocol.

Stop-construction semantics (from `strategy/adaptive_trend.py`,
`_long_stop`/`_short_stop`): `stop = pivot ∓/± sr_stop_buffer`, then the
resulting distance is floored at `sr_min_stop_distance` and capped at
`max_stop_distance` (31, CLOSED axis). The two parameters interact
directly: a larger buffer pushes more stops past the floor (making the
floor irrelevant on those trades); a larger floor overrides small
pivot distances (making the buffer irrelevant on those). Joint cells
like (18, 7) have never been tested anywhere.

## Two on-record justifications

1. **Python authority for a stop-distance change.** The TV-era rule
   "stop/exit changes are TV-only" (`strategy-audit` Standard 10)
   targeted CSV exit *approximations* — post-hoc trade-list surgery
   that cannot model exit→re-entry cascades. This engine does neither:
   stops are simulated bar-by-bar inside the fill simulator, and the
   engine is TV-reconciled at $0.00 entry-price delta (106/106 trades,
   `docs/decisions/2026-07-04-python-baseline-anchor.md`). The rule's
   underlying concern does not apply; per
   `feedback_python_reopens_closed_axes`, Python is authoritative here.
2. **The TV-era single-axis hints do not pre-answer this sweep.** The
   2026-07-03 1-contract sweep (minSR 18: +$6,035, Welch t=+0.11 n.s.;
   buffer 7: +$2.7K, flat) ran on the FLAT engine — no anti-martingale,
   no DLL. The squeeze-internals episode
   (`feedback_squeeze_internals_closed`) showed results can flip sign
   between DLL-off and DLL-on. This sweep is therefore the first test
   of these axes under the production AM+DLL config at all — genuinely
   open, not a re-run of settled evidence.

## Pre-registered experimental design (locked by this spec, before any cell runs)

- **Grid:** `sr_min_stop_distance ∈ {10.0, 12.0, 15.0, 18.0, 20.0}` ×
  `sr_stop_buffer ∈ {3.0, 5.0, 7.0, 9.0}` — 20 cells including the
  baseline (15.0, 5.0) as the empty override dict `{}`. Values are
  floats, matching the config field types exactly (both fields are
  `float`; an int override would change `parameter_hash` semantics
  without changing behavior). No adaptive refinement; the grid runs
  once as registered.
- **Everything else identical to the MA sweep, by reference:**
  truncated bar window 2022-11-01 → 2025-07-01 (validated 2026-07-05
  to reproduce the full-history train baseline exactly: n=378,
  net=$65,855); train slice 2023-01-01 → 2025-06-30 by entry
  timestamp; cost model from
  `scripts.freeze_baseline_anchor.FROZEN_SIMULATION_OVERRIDES`; base
  config `production_am_config()`; all scored promotion rows as
  implemented in `research/sweep.py:score_cell` including the
  session-level paired t per the 2026-07-05 Phase 0 amendment;
  selection rule `select_qualifier` (one best cell by net P&L among
  all-row passers, baseline excluded, None otherwise).
- **Row 8 (slippage 0.5pt/1.0pt) runs only for a selected qualifier,
  before holdout. The harness never touches holdout.**
- **Interpretation guardrails, in advance:** a "wider is better"
  gradient without a full-row passer is reported and the axis CLOSES —
  no intermediate values, no re-formed tests, no "close enough," no
  holdout peek (same rules applied to the MA near-miss). A qualifier
  that later fails holdout closes the axis pair entirely; the
  runner-up does not get a holdout attempt.

## Code

One new driver + one new test. No changes to `research/sweep.py`, the
MA driver, or any `src/full_python` production module.

- `scripts/sweep_sr_interaction.py` — clone of
  `scripts/sweep_ma_lengths.py` with:
  - `GRID_SR_MIN = (10.0, 12.0, 15.0, 18.0, 20.0)`,
    `GRID_SR_BUF = (3.0, 5.0, 7.0, 9.0)`,
    `BASELINE_CELL = (15.0, 5.0)`
  - override keys `"sr_min_stop_distance"` / `"sr_stop_buffer"`
  - `OUT_DIR = runs/sweeps/sr-grid`
  - cell naming `srmin_<int>_srbuf_<int>` (values are integral floats;
    file names use `int(value)`)
  - scoreboard/CSV column headers renamed (`sr_min`, `sr_buf`)
  - unchanged: bars loading, error handling (missing bars file exits 1;
    baseline-cell error exits 1; per-cell errors recorded and skipped),
    scores.csv/summary.json structure, qualifier verdict text
- `tests/test_sweep_sr_driver.py` — pins the grid literals and
  `build_grid()` shape: 20 cells, `{}` exactly once, 20 distinct
  (min, buf) pairs, baseline pair present. Same pattern as
  `tests/test_sweep_driver.py`.

Duplication of the ~100-line driver body is deliberate (approved
Approach A): the MA driver is retired (its axis closed), this is the
last open axis so no third consumer exists, and the logic that must
stay bit-identical across sweeps — scoring — is already shared in
`research/sweep.py`. YAGNI over DRY for a finale-copy of an archived
original.

## Testing

The single pinning test above. The harness core is already covered by
`tests/test_sweep.py` (17 tests); no new core behavior is introduced.
Error-path verification (missing bars file → ERROR + exit 1) is run
manually in the worktree, which has no `runs/` data — same check the
MA driver task used.

## Evaluation path (after the sweep runs)

1. No qualifier → closing decision doc; Phase 4's axis map is fully
   closed and Gate 1 moves to whatever the next protocol phase demands.
2. Qualifier → row 8 slippage runs for that cell only → evaluation doc
   → deliberate, user-approved one-shot holdout per Phase 0.
