# First Baseline Milestone

## Decision

The first Full Python milestone is a reproducible baseline replay report, not optimization and not live execution.

## Evidence

- Canonical CSV data boundary exists.
- Data manifest hash exists.
- Strategy config hash exists.
- Baseline strategy emits accepted and rejected decisions.
- Replay logs bars, signals, rejections, and order intents.
- Event log persists to JSONL.
- Baseline CLI writes `events.jsonl` and `report.json`.

## Constraints

- Risk validation remains MNQ-first.
- RTH candidates are the first promotion target.
- Pine remains reference material only.
- No broker adapter is included in this milestone.

## Next Review Trigger

After the first real NQ/MNQ historical file successfully produces a baseline report, review whether to port ATF, support/resistance, prove-it, and squeeze primitives into the baseline strategy.
