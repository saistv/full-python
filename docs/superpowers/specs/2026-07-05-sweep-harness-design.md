# Gate 1 Sweep Harness — Design

## Context

Gate 1 Phase 4 (per `docs/decisions/2026-07-05-gate1-phase0-protocol.md`)
requires sweeping the OPEN `AdaptiveTrendConfig` axes: `ma_50_length` ×
`ma_200_length` first, then the `sr_min_stop_distance` × `sr_stop_buffer`
interaction. The prior-vol-gate evaluation
(`docs/decisions/2026-07-05-prior-vol-gate-evaluation.md`) proved the
promotion-table + one-shot-holdout discipline works; this harness makes
the same scoring mechanical and identical across all sweep jobs instead
of re-implemented per sweep in one-off scripts.

Approved architecture: reusable library module + thin per-sweep driver
scripts (Approach A). The scoring logic — the part that must be
bit-identical across sweeps — lives in one tested place.

## Goal

A tested `run grid → score cells → select qualifier` library, plus a
driver script that pre-registers and runs the 5×5 MA-length grid on the
train window and reports which cell (if any) qualifies for holdout.

## Pre-registered experimental design (locked by this spec, before any cell runs)

- **Grid:** `ma_50_length ∈ {30, 40, 50, 60, 70}` ×
  `ma_200_length ∈ {100, 150, 200, 250, 300}` — 25 cells, including the
  baseline cell (50, 200). No adaptive refinement around peaks; the grid
  runs once as registered.
- **Data:** truncated bar series 2022-11-01 → 2025-07-01 sliced from
  `runs/multi-year/nq1_2021-03-16_2026-06-26.csv`, trades filtered to
  the train window 2023-01-01 → 2025-06-30 by entry timestamp. (The
  truncated start was validated on 2026-07-05 to reproduce the
  full-history baseline exactly: n=378, net=$65,855.)
- **Cost model:** the frozen one — `point_value=20`,
  `commission_per_contract_round_trip=10`, `entry_slippage_points=0.75`,
  `exit_slippage_points=0.75`, `rth_open_extra_entry_slippage_points=0.0`.
- **Base config:** `production_am_config()`.
- **Selection rule:** among cells that pass ALL scored rows, the single
  best-by-net-P&L cell is the only candidate that may proceed to the
  slippage row and then the one-shot holdout. If it fails holdout, the
  ENTIRE axis pair closes — the runner-up does not get a holdout attempt
  (serial holdout attempts are holdout mining). If no cell qualifies,
  the axis pair closes on train evidence alone.
- **Row 8 (slippage sensitivity) is not scored per-cell** — it triples
  compute for information only the qualifier needs. It runs only for the
  selected qualifier, before holdout.
- **Holdout is never run by the harness.** It remains a deliberate,
  human-initiated step after train results are reviewed.

## Architecture

New package `src/full_python/research/` with one module, `sweep.py`.
Research tooling stays out of the production replay path (`cli.py`,
`simulation/`, `strategy/` are untouched).

### `src/full_python/research/sweep.py`

Three units plus two frozen dataclasses:

```python
@dataclass(frozen=True)
class CellResult:
    overrides: dict          # {} for the baseline cell
    trades: list[Trade]      # train-window trades only
    error: Optional[str]     # non-None if the simulation raised

@dataclass(frozen=True)
class CellScore:
    overrides: dict
    trade_count: int
    net_pnl: float
    delta_vs_baseline: float
    rows: dict               # row name -> {"pass": bool, ...numbers}
    passes_all: bool         # True iff every scored row passes
```

**`run_grid(bars, base_config, overrides_list, sim_config, train_start, train_end) -> list[CellResult]`**

For each override dict: build
`AdaptiveTrendConfig(**{**base_config.to_dict(), **overrides})`, run a
fresh `AdaptiveTrendStrategy` through `SimulationEngine(sim_config)` on
the shared `bars` list (loaded once by the caller), slice trades to
`train_start <= entry_timestamp_utc < train_end`, return `CellResult`.
The baseline cell is the empty dict `{}` and flows through the identical
path — baseline and cells cannot diverge in cost model or slicing. A
cell whose simulation raises is captured as `error=str(exc)` and the
grid continues; it is never silently dropped.

**`score_cell(cell, baseline) -> CellScore`**

Scores the mechanically-computable train promotion rows from Phase 0.
Every row records its numbers, not just pass/fail:

| Row | Rule | Implementation |
|---|---|---|
| 1. Materiality | net delta ≥ +$10,000 | `sum(cell) - sum(baseline)` |
| 2. Expectancy | ≥ +10% | net/trade_count each side, relative change |
| 3. Trade count | doesn't drop >20% | **flag-only**: a drop >20% sets `"needs_justification": True` but does not fail the cell — justification is a human judgment; the harness reports, the evaluation doc decides |
| 4. Max drawdown | doesn't worsen >15% | running-equity max drawdown over the train trade sequence, computed identically for cell and baseline |
| 5. Outlier survival | still ahead after top-1/2/3 removal | remove each population's OWN top winners (cell's from cell, baseline's from baseline); cell must remain ahead of baseline at all three cuts |
| 6. Year-by-year | ≥2 of 3 years better-or-neutral | calendar year of entry timestamp: 2023, 2024, 2025(H1) |
| 7. Side symmetry | not carried by one side | long delta ≥ 0 AND short delta ≥ 0 — a cell that gains on one side by losing on the other FAILS this row, regardless of net |
| 9. Significance | \|t\| ≥ 2.0 | **session-level paired t** (below) |

Row 9 implementation: aggregate net P&L per `session_date` for cell and
baseline over the UNION of sessions where either has a trade (absent
session = $0.0). Compute per-session differences `d_i = cell_i −
baseline_i`, then `t = mean(d) / (stdev(d) / sqrt(n))` using sample
stdev, dropping the degenerate case stdev=0 (identical populations →
t undefined → row fails, correctly: no detectable difference). This is
a paired test, valid for the heavily-overlapping trade populations a
config tweak produces — NOT an unpaired Welch t between the two trade
lists, which treats overlapping populations as independent samples (the
error class documented in `feedback_mc_comparison_rules` and flagged in
the prior-vol evaluation's own setup).

`passes_all` requires rows 1, 2, 4, 5, 6, 7, 9 to pass (row 3 is
flag-only, row 8 deferred to the qualifier).

**`select_qualifier(scores) -> Optional[CellScore]`**

The pre-registered selection rule as code: filter to `passes_all`,
return max by `net_pnl`; `None` if empty. Baseline's own cell (empty
overrides) is excluded — it cannot qualify against itself.

### `scripts/sweep_ma_lengths.py`

Thin driver, ~60 lines:

1. Grid literals exactly as pre-registered above (the 25 override
   dicts, generated from the two value lists, with `{}` for (50, 200)).
2. Load bars once, slice to 2022-11-01 → 2025-07-01.
3. `run_grid(...)`, then `score_cell(...)` per cell, then
   `select_qualifier(...)`.
4. Write outputs (below), print a human-readable scoreboard sorted by
   net P&L with pass/fail per row, and the qualifier verdict.
5. Exit. No slippage runs, no holdout — those are follow-up steps.

## Outputs

All under `runs/sweeps/ma-grid/` (gitignored like all `runs/*`):

- `cells/ma50_<X>_ma200_<Y>.trades.csv` — per-cell train trades, same
  column schema as existing trades.csv exports, so `analyze`-style
  re-analysis needs zero re-runs.
- `scores.csv` — one row per cell: override values, error (if any),
  trade_count, net, delta, each row's key numbers and pass/fail,
  passes_all.
- `summary.json` — the grid definition as registered, data window,
  cost-model dict, baseline numbers, per-cell score dicts, qualifier
  overrides or null, ISO timestamp. The machine-readable record of what
  was pre-registered and what happened.

## Error handling

- A raising cell → `error` recorded in `CellResult`, `scores.csv` row
  carries the error string, sweep continues.
- Baseline cell erroring is fatal (nothing can be scored) — the driver
  exits nonzero with the error.
- Bars file missing → driver exits nonzero with the path it looked for.

## Testing

Unit tests in `tests/test_sweep.py`, synthetic data only (no 5-year
CSV, no SimulationEngine in scoring tests):

1. `score_cell` on a hand-built baseline + cell where every row's
   pass/fail is hand-computable; assert each row's verdict and numbers.
2. A cell constructed with an outlier-carried gain: passes 1/2/4/6/7,
   fails 5 AND 9, `passes_all` False. (Amended during planning: a gain
   concentrated in ~3 sessions out of 18 mathematically cannot clear
   the paired-t bar, so rows 5 and 9 co-fail by design — the harness
   catches outlier-carried gains twice. Row 5's isolated cut logic is
   covered separately by a direct unit test on the top-N helper.)
3. Paired-t fixture: known session P&L series with an independently
   hand-computed t (verified against `statistics` in the test); assert
   row 9's t matches to 1e-9 and the pass threshold behaves at the
   boundary.
4. Row 3 flag-only behavior: a >20% trade-count drop sets the flag but
   does not affect `passes_all`.
5. `select_qualifier`: none qualify → None; several qualify → the
   best-by-net wins; baseline cell never returned even if it trivially
   "passes".
6. Integration smoke: `run_grid` with a 2-cell grid (baseline + one
   `ma_50_length` override) on a small synthetic bar series; asserts
   both cells produce `CellResult` without error, the baseline cell's
   config hash equals `production_am_config()`'s, and the override
   cell's differs.

## Explicitly out of scope

- Running the actual 25-cell sweep (follow-up, after the harness lands
  and its tests pass).
- Row 8 slippage runs, the evaluation doc, and the holdout step.
- The `sr_min_stop_distance` × `sr_stop_buffer` sweep (next job, same
  harness, own driver + own pre-registered grid).
- Parallelism (sequential ~17 min is acceptable; YAGNI).
- Any change to `strategy/`, `simulation/`, `risk/`, `regime.py`, or
  `cli.py`.

## Evaluation path (after the sweep runs)

1. Review the scoreboard. If no qualifier → write the closing decision
   doc; the MA axis pair is closed on train evidence.
2. If a qualifier exists → run row 8 (0.5pt / 1.0pt slippage) for it
   alone; if it survives, write the evaluation doc and then — as a
   separate, deliberate step — the one-shot holdout per Phase 0. Sign
   reversal or material degradation on holdout closes the axis pair
   entirely (no runner-up attempts).
