# Session-End Exit Correction - 2026-07-02

## Discovery

While diagnosing short-side behavior, we found a major replay assumption gap:

`--session rth` filtered bars to RTH, but the simulator did not force positions flat at the end of each RTH session.

That meant trades could enter during one RTH session and exit on a later RTH session, silently carrying overnight/weekend exposure through gaps where no bars were replayed.

## Code Change

Added explicit session-end exit support:

- Simulator parameter: `exit_at_session_end`
- CLI flag: `--exit-at-session-end`
- Sweep flag: `--exit-at-session-end`
- Exit reason: `session_end`

When enabled, an open trade exits at the prior bar's close when the next bar belongs to a new New York session date.

Default remains unchanged so old research artifacts can still be reproduced, but serious intraday research should use `--exit-at-session-end`.

## Impact On Prior Lead Candidate

Candidate A long-only settings:

- Activation: `30`
- Giveback: `20`
- Fresh breakout clearance: `0.5`
- Cooldown: `0`
- Long-only

| Mode | Trades | Net P&L | Max DD | Max Loss Streak | P&L Without Best 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Prior behavior, allowed session carry | 477 | $3,728.50 | -$802.00 | 6 | $1,853.50 |
| Forced session-end exit | 612 | -$2,801.00 | -$3,520.50 | 12 | -$4,033.00 |

Carry contribution under prior behavior:

- Total P&L: `$3,728.50`
- Same-day trades only: `-$1,344.50`
- Session-carry trades: `+$5,073.00`
- Session-carry trade count: `73`

So the prior long-only lead was not a clean intraday edge. It depended heavily on holding through session boundaries.

## Impact On Best Short-Only Exit Branch

Best short-only exit branch from the prior sweep:

- Activation: `20`
- Giveback: `10`
- Fresh breakdown clearance: `0.5`
- Cooldown: `0`
- Short-only

| Mode | Trades | Net P&L | Max DD | Max Loss Streak | P&L Without Best 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Prior behavior, allowed session carry | 89 | $279.00 | -$305.50 | 3 | -$322.50 |
| Forced session-end exit | 90 | $22.50 | -$305.50 | 3 | -$366.50 |

The short branch was less dependent on session carry than the long branch, but the small positive result still is not robust.

## Interpretation

This is a major correction to the research baseline.

Going forward, we need two separate research modes:

1. **Intraday-flat mode:** use `--exit-at-session-end`; this is the correct mode if the intended automation should not hold overnight.
2. **Swing/overnight mode:** omit `--exit-at-session-end`; this must be treated as a different strategy with explicit overnight risk assumptions.

The project goal has been hands-off intraday NQ/MNQ automation. Therefore, intraday-flat mode should be the default decision-grade mode.

## Next Required Validation

Rerun the leading long-side and short-side sweeps with `--exit-at-session-end`.

Do not promote any candidate from the prior sweeps until it survives this corrected intraday assumption.

