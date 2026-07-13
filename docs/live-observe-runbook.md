# Gate 5 — Observe Session Runbook

Attended demo-observe session on the operator's Mac. Orders are
impossible by construction (observe literals in
`full_python/live/runner.py`; sentinel REST client).

## One-time setup

Export the seven credential variables in the shell that runs the
session (never commit them, never echo them):

    export TRADOVATE_USERNAME=...
    export TRADOVATE_PASSWORD=...
    export TRADOVATE_APP_ID=...
    export TRADOVATE_APP_VERSION=...
    export TRADOVATE_CLIENT_ID=...
    export TRADOVATE_SECRET=...
    export TRADOVATE_DEVICE_ID=...

## Per session

1. Start any time after ~9:00 ET (before the 9:30 window):

       python3 -m full_python.live

   Options: `--data-dir runs/live` (default), `--end-et 16:05`,
   `--bars-back 400`, `--symbol-root NQ`.
2. Watch the console. Every bar logs one line; signals log as
   `SIGNAL`/`EXIT`; halts are loud `HALT:` lines with the reason.
   If the first finalized bar does not arrive by the current
   minute-plus-grace deadline while the configured active window is armed,
   the runner exits nonzero with `reason="data_outage"`. It still writes the
   ledger and HTML report; do not count that session as clean.
3. End: Ctrl+C anytime, or the runner stops itself at `--end-et`.
   A flat observer with no bars outside the active window still stops exactly
   at this wall-clock end. An open position never takes that quiet-stop path.
   Either way the runner writes artifacts and prints the parity verdict.
4. Rebuild a report later: `python3 -m full_python.live --report-only
   runs/live/<date>/events.jsonl`.

Note: a second run on the same day writes `events-2.jsonl` / `report-2.html` (and so on) — one ledger file per run; the runner picks the fresh name automatically.

## Artifacts (per session, under `runs/live/<session-date>/`)

- `events.jsonl` — full event ledger, one per run (crash-safe, append-per-event)
- `account_risk.json` — GET-only risk probe (autoLiq = the DLL evidence)
- `report.html` — shadow parity report (verdict, signals, halts, sim info)

## Gate 5 pass criteria (pre-registered in the spec)

3 clean sessions, each with: exact PARITY verdict; every
disconnect/outage handled by the documented halt policy; probe output
captured. Divergent or unexplained sessions do not count and open a
bar-level debug from the ledger.

Before counting the first session, perform attended failure drills in DEMO:

1. Start inside the active window with market data unavailable. Confirm exit
   code `2`, a console `HALT: data_outage`, an `execution_halt` ledger record,
   and a report listing `data_outage`.
2. Interrupt the market-data connection after bars begin. Confirm a nonzero
   exit and a report on all bars recorded before the disconnect.
3. Start before the active window and confirm delayed initial data is accepted;
   then run flat with no data outside the active window and confirm automatic
   completion at `--end-et`.

Archive each drill's redacted ledger/report next to the session evidence. These
drills validate detection and artifacts; they do not count toward the three
clean sessions.

| # | Date | Verdict | Halts (reason) | Probe captured | Clean? |
|---|------|---------|----------------|----------------|--------|
| 1 |      |         |                |                |        |
| 2 |      |         |                |                |        |
| 3 |      |         |                |                |        |
