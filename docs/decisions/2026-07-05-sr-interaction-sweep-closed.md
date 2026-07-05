# S/R Interaction Sweep (sr_min × sr_buffer) — CLOSED, No Qualifier

Second and final run of the Gate 1 Phase 4 sweep harness
(`scripts/sweep_sr_interaction.py`, spec:
`docs/superpowers/specs/2026-07-05-sr-interaction-sweep-design.md`,
merged via PR #7). Grid, windows, cost model, scoring rows, and the
selection rule were pre-registered before any cell ran. Full outputs:
`runs/sweeps/sr-grid/` (gitignored, reproducible).

**Result: no cell passed every scored row — the best cell fails even
the materiality bar. Per the pre-registered rule, the
`sr_min_stop_distance` × `sr_stop_buffer` axis pair CLOSES on train
evidence. Nothing advances to slippage runs or holdout. This was the
last open Phase 4 axis: the axis map is now fully resolved.**

## Scoreboard (train 2023-01-01 → 2025-06-30, 20 cells, top and bottom)

| sr_min | sr_buf | n | net | delta | paired t | verdict |
|---|---|---|---|---|---|---|
| 18 | 3 | 374 | $74,505 | **+$8,650** | 1.24 | best cell — fails materiality ($10K) AND significance (2.0) |
| 18 | 5 | 372 | $72,350 | +$6,495 | 1.13 | fails both |
| 12 | 3 | 380 | $71,850 | +$5,995 | 1.19 | fails both |
| 15 | 5 | 378 | $65,855 | — | — | baseline |
| 10 | 5 | 378 | $56,895 | -$8,960 | -1.05 | worst cell |

Max |t| anywhere in the grid: 1.24. No cell cleared materiality; no
cell came close to the significance bar.

## Findings

1. **The TV-era minSR hint reproduces in direction, not in force.**
   The flat-engine 2026-07-03 sweep found minSR=18 worth +$6,035
   (Welch t=+0.11, n.s.). Under the production AM+DLL config, (18, 5)
   shows +$6,495 (paired t=1.13) — same direction, still nowhere near
   either bar of the materiality test. The hint was real but small,
   and it stays small.
2. **The buffer=7 hint mildly REVERSES under AM+DLL.** The flat-engine
   sweep suggested buffer 7: +$2.7K. Under the production config,
   every buffer=7 cell is at or below baseline ((15,7): -$1,525).
   A small-scale instance of the DLL-on/off sign-flip phenomenon
   already documented in `feedback_squeeze_internals_closed` —
   further evidence that flat-engine results do not transfer to the
   production sizing stack.
3. **The interaction Phase 0 worried about exists but is tiny.** The
   joint wide-narrow cell (18, 3) beats both its single-axis parents
   ((18, 5): +$6,495; (15, 3): +$3,245) — so the interaction is real
   in sign, Standard 15 was right to demand the joint check, and the
   answer is: +$8,650 at best, t=1.24, below every bar. Checked,
   quantified, closed.
4. **No degenerate behavior anywhere:** all 20 cells ran clean (no
   errored cells), trade counts vary sensibly (370-380), and the
   response surface is smooth — no lone spikes of the kind the
   neighbor-coherence concern anticipated.

## Protocol compliance

Same discipline as the MA sweep closure
(`docs/decisions/2026-07-05-ma-length-sweep-closed.md`): no
intermediate values chased, no re-formed tests, no holdout peek, no
"best cell as a lead." The (18, 3) cell is reported and closed.

## Gate 1 Phase 4 — final status

| Axis | Status | Evidence |
|---|---|---|
| `fallback_stop_points` | CLOSED | Phase 2 diagnosis: 2.1% incidence, cannot reach materiality |
| `ma_50_length` | CLOSED | non-binding across 30-70 (byte-identical trades) |
| `ma_200_length` | CLOSED | best cell (100) +$18,085 fails t=1.75 < 2.0 |
| `sr_min_stop_distance` × `sr_stop_buffer` | CLOSED | best cell (18,3) +$8,650 fails materiality and t=1.24 < 2.0 |

**Phase 4 conclusion: the production config survives its full
Python-era parameter audit unchanged.** Every open axis was swept
under the pre-registered protocol with the production AM+DLL sizing
stack, and no candidate cleared the promotion table's train rows —
most failed early. Combined with the prior-vol gate's holdout
rejection, Gate 1's output to date is: zero config changes promoted,
several TV-era ambiguities resolved with real statistics, and a
reusable, tested harness + protocol for any future candidate. The
locked config's robustness is now evidenced in Python, not just in
TV-era manual testing.
