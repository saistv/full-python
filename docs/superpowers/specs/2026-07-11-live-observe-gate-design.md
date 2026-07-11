# Live Observe Gate (SP4 slice 1) — Design

Date: 2026-07-11
Status: approved (brainstorm 2026-07-11)
Parent: `2026-07-05-execution-core-design.md` (gate ladder),
`2026-07-07-tradovate-adapter-design.md` (adapter + open decisions),
`2026-07-10-tradovate-gap-closure-design.md` (broker safety model).

## Purpose

First slice of sub-project 4 (Gate 5/6/7 operational tooling): **Gate 5,
demo observe**. A live session runner connects to the Tradovate DEMO
environment with real credentials, streams NQ 1-minute bars through the
existing `LiveBarSource` → `LiveLoop` path with the production strategy,
persists every event to disk as it happens, and **cannot place orders by
construction**. After each session, a shadow report replays the session's
bars through `SimulationEngine` and diffs the sim's decisions against the
live-recorded ones — the live/sim identity gate, now on live data.

Explicitly OUT of scope (each gets its own later spec, informed by what
observe sessions teach us): the demo order test (`order_enabled=True`
anywhere), reconnect/retry logic, the paper gate, reconciliation tooling
beyond the shadow diff, the MNQ pilot, any VPS/unattended operation.
Runtime context for this slice: attended, operator-started terminal
session on the operator's Mac.

## Decisions locked by this spec

1. **Observe mode is the only mode.** The runner constructs
   `TradovateBroker` with `order_enabled=False` and
   `flatten_enabled=False` as literals in the composition root — not
   read from env or CLI. A test pins that the runner cannot produce an
   order-capable broker. (The broker constructor independently forbids
   `order_enabled` without `daily_loss_limit` + `flatten_enabled`;
   observe mode never gets there.)
2. **Demo environment only.** The runner hardcodes the demo
   `TradovateEnvironment`. Pointing at live is a code change belonging
   to a later spec, not a flag.
3. **No silent reconnects.** Any transport/auth/data failure surfaces
   as the documented halt/fatal path with its reason on the console and
   in the ledger. For Gate 5, *seeing* failures is the point; reconnect
   policy is designed in the order-test spec from observed failure
   modes.
4. **Success criteria (pre-registered):** the gate passes after **3
   clean demo sessions** where
   (a) the shadow report shows exact signal parity (same entry intents,
   same minutes, same sides; same exits where the sim's position opens),
   (b) every disconnect/outage/anomaly that occurs is handled by the
   existing halt policy (halt with reason; no undefined behavior), and
   (c) the account risk probe output is captured. Sessions with
   divergence or unexplained behavior do not count and open a bar-level
   debug (the May-13 precedent: event ledger first).
5. **Observe-mode "signals" = the ledger record** of strategy intents
   and their `Rejected(reason="order_disabled")` lifecycle notices,
   plus exit decisions. No broker changes are needed: with orders
   disabled the existing machinery already records what the strategy
   would have done, and `OrderStateMachine` treats `Rejected` as a
   lifecycle notice (no position effect).

## Components

### 1. `src/full_python/tradovate/transport.py` — real WebSocket transport

The only genuinely new low-level component. Stdlib-only RFC 6455 client
over `socket` + `ssl`, implementing the existing `WebSocketTransport`
protocol (`send(frame)`, `receive(timeout_seconds)`, `close()`), so
`TradovateWebSocketClient`, `TradovateMarketDataFeed`, and every
existing test are untouched.

- **Handshake:** HTTP/1.1 Upgrade with random `Sec-WebSocket-Key`;
  validates `101` status and the `Sec-WebSocket-Accept` SHA-1 echo.
  Connect/handshake bounded by a timeout.
- **Frames:** client-to-server text frames masked (per RFC); handles
  server text frames, fragmented messages (continuation frames),
  `ping` → immediate `pong`, and the close handshake (responds with
  close, then `TradovateWebSocketError` on further receive).
  Binary frames are unexpected from Tradovate → error, never guess.
- **Timeouts:** every receive uses the caller's `timeout_seconds` via
  `socket.settimeout`; timeout returns `None` (matching the protocol's
  contract used by `feed.next_bar`). No infinite waits anywhere.
- **Heartbeats:** Tradovate's SockJS-style `h` frames are answered at
  the transport level (send `[]`) and NOT surfaced to the framing
  layer, keeping `TradovateWebSocketClient` pure. The `o` open frame is
  likewise consumed during connect.

### 2. `src/full_python/live/runner.py` — composition root + CLI

New package `full_python/live/`; invoked as `python3 -m full_python.live`.
Wiring order:

`credentials_from_env()` → REST auth client (existing, with its
token-renewal lead; the runner renews on schedule between bars) →
`transport.py` connect → `TradovateWebSocketClient.authorize` →
`TradovateMarketDataFeed` → `ContractAuthority` (front contract by the
verified expiry−3-business-days roll rule) → `LiveBarSource` (production
`ActiveWindow` from config, session-armed outage policy) →
`TradovateBroker` (observe literals per Decision 1; `dollar_point_value`
= 20.0 NQ; `daily_loss_limit=None` is valid because orders are off) →
`RiskSupervisor` (point_value 20.0, measurement posture) → `LiveLoop`.

Lifecycle: start any time after ~9:00 ET; subscribe with a short
`bars_back` backfill so indicator state warms the same way the sim
does; run the risk probe once at startup; stream until **Ctrl+C**
(SIGINT handler → clean shutdown) or the configured session end
(default 16:05 ET wall clock). Either exit path flushes the ledger,
prints the artifact paths, and immediately runs the session report so
the parity verdict lands in the same terminal.

CLI surface (deliberately small): `--data-dir` (default `runs/live`),
`--end-et HH:MM` (default 16:05), `--bars-back N` (default sized to the
slowest indicator warm-up), `--symbol-root NQ`. No flag can enable
orders.

Console output via stdlib `logging`: one line per bar (ET time, close,
position, session P&L), one per intent/rejection/exit, unmissable lines
for halts with reason, startup summary of the resolved contract and
config hash.

### 3. Ledger persistence — append-as-you-go JSONL sink

`EventLedger.write_jsonl` writes only at the end; a crash would lose
the session record. Add a small sink so each appended event is written
and flushed to `runs/live/<session-date>/events.jsonl` immediately.
Implementation choice for the plan: either an `EventLedger` subclass or
a sink callback on append — whichever stays smallest; behavior pinned
by a test that kills the writer mid-session and finds all prior events
on disk. File format identical to `write_jsonl` so
`EventLedger.read_jsonl` reads it unchanged.

### 4. `src/full_python/live/session_report.py` — shadow diff

Post-session: read the session JSONL, extract the bars, replay them
through `SimulationEngine` with the production strategy
(`production_am_config()`), and diff minute-by-minute:

- sim entry decisions vs live-recorded intents (minute, side, quantity);
- sim exit decisions vs live-recorded exits, **only where positions
  exist on both sides** (with orders off, the live side never holds a
  position, so exits compare on the sim's timeline of the *recorded*
  intents — the diff is signal-level, not fill-level);
- verdict line: `PARITY` or `DIVERGENCE at <ET minute>: <sim> vs <live>`
  per mismatch.

Output: console verdict + an HTML report (reusing the existing report
rendering helpers) written next to the JSONL. Exit code nonzero on
divergence so a wrapper script can notice.

### 5. `src/full_python/live/risk_probe.py` — account risk snapshot

Read-only REST GETs at startup — `account/list`,
`cashBalance/list`, `userAccountAutoLiq/list` (the
account-level auto-liquidation settings, the direct evidence for the
DLL question), and `marginSnapshot/list`; endpoints that 404/403 on
demo are recorded as such rather than failing the run — dumped
verbatim to `runs/live/<session-date>/account_risk.json`. **GET only;
the probe never POSTs.** (Amended at plan time: the snapshot endpoint
is a POST; the GET-only rule outranks the endpoint list.) Purpose: empirical input to the open
operational decision "does Tradovate/the prop firm enforce an
account-level daily-loss limit, and does it force-flatten or only block
new orders" (parent spec, Open Operational Decisions). The probe
records; interpretation happens in the order-test spec.

## Error handling

Inherited unchanged from the existing policy:

- Data outage inside the armed session → `LiveDataError` → LiveLoop
  halts and flattens (a no-op in observe) with `reason="data_outage"`.
- `ExecutionInvariantError` (incl. `TradovateStateError`) → halt
  WITHOUT flatten, `reason="invariant_violation"`.
- Transport/auth/WS failures → fatal exception; the runner catches at
  top level only to log the reason, flush the ledger, and run the
  report on whatever was recorded, then re-raises with nonzero exit.
- Credentials are never logged; the existing redaction discipline
  applies to the transport and probe (tokens never appear in JSONL,
  console, or `account_risk.json`).

## Testing

Offline, existing style (pytest, fake transports, stdlib only):

- **RFC 6455 unit tests** against fixed vectors: handshake request
  shape + accept validation, mask application, frame encode/decode,
  fragmented message reassembly, ping→pong, close handshake, timeout →
  `None`, binary frame → error. Fake socket object; no network.
- **Stack test:** scripted fake socket drives transport →
  `TradovateWebSocketClient` → `TradovateMarketDataFeed` →
  `LiveBarSource` producing `MarketBar`s.
- **Composition pin:** the runner's builder, on fakes, yields a
  `LiveLoop` whose broker has `order_enabled=False` /
  `flatten_enabled=False`; no code path or CLI argument can flip them
  (test greps the constructed config, not the source).
- **JSONL sink crash test:** events written before a simulated crash
  are complete and parseable on disk.
- **Shadow report goldens:** a scripted identical session → `PARITY`;
  a scripted one-minute divergence → detected, correct minute, nonzero
  exit.
- **Risk probe:** fake REST client → GET-only assertion + snapshot file
  shape.

The real socket path is deliberately proven by the demo sessions
themselves — that is what Gate 5 is. No mock-server integration test.

## Acceptance criteria

This slice is done when:

- `python3 -m pytest -q` green with all the above tests;
- `python3 -m full_python.live` runs against the demo environment on
  the operator's Mac, streams the session, writes
  `events.jsonl` + `account_risk.json` + the HTML shadow report;
- observe mode is pinned untamperable by test;
- the Gate 5 counter starts: 3 clean sessions per Decision 4 pass the
  gate and unlock writing the demo-order-test spec.
