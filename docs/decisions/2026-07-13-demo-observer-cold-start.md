# Demo Observer Cold-Start Remediation

**Decision:** remove the principal audit's P1-03 demo-observation blocker by
making startup outage detection clock-authoritative and bounding no-data
sessions by the configured ET end. This change does not enable orders and does
not pass Gate 5 by itself.

Authority audit:
`docs/audits/2026-07-13-principal-adversarial-red-team-audit.md`.

## Failure

Before the first emitted bar, `LiveBarSource` used `expected=None`.
`_armed(None)` was false for a flat observer, even when the injected clock was
inside the active window. Repeated feed timeouts could therefore loop forever,
and the runner could not reach its wall-clock end check or produce a report.

## Policy

- Cold start expects the injected clock's current UTC minute.
- Its deadline is that minute plus 60 seconds plus the configured grace.
- A timeout or first bar arriving after that deadline raises
  `DataOutageError` when the active window or an open position arms detection.
- Before the active window, a timeout may be followed by a valid first bar.
- Outside the active window while flat, no data may wait only until the
  configured ET session end. Poll time is capped at that exact deadline.
- At or after session end, a flat source terminates cleanly without polling.
- An open position never terminates through the quiet session-end path; missing
  data remains an outage.
- The live loop persists `transition="execution_halt"` with
  `reason="data_outage"`; the top-level runner closes resources and renders the
  report even when no bar was received.

## Acceptance Evidence

- Starts before, inside, and after the active window are covered offline.
- No first bar, a first bar after deadline, a delayed valid first bar, exact
  configured-end completion, and open-position behavior are covered.
- The observe composition test proves a cold-start outage closes the socket,
  returns exit code 2, persists the halt, and renders a `NO-DATA` report with
  the outage reason.
- Focused observer suite: 28 passed.
- Full offline suite: 420 passed, 4 operator-data tests skipped.
- Full suite with `FULL_PYTHON_BASELINE_DATA`: 424 passed.

## Remaining Gate

Gate 5 remains open. The operator must complete the attended DEMO
disconnect/outage drills and preserve three nonconsecutive clean sessions with
independent bar verification and exact signal parity. Demo orders, paper,
funded MNQ, and unattended production remain prohibited.
