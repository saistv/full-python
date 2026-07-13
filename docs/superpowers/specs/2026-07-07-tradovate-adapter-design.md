# Tradovate Adapter (Live-Engine Sub-Project 3) — Design

## Context

This is live-engine sub-project 3, following:

1. execution core — done and merged;
2. live data feed + contract authority — done and merged;
3. Tradovate adapter — this spec;
4. Gate 5-7 operational tooling — paper, reconciliation, dashboarding,
   and the tiny MNQ live pilot.

The current system can replay and paper-trade deterministically, but it
cannot place real orders. The `HANDOFF.md` guardrail remains binding:
**no live broker adapter exists yet, and nothing may claim live trading
capability until this sub-project is implemented and gated.**

The goal here is to implement the Tradovate-specific side behind the
already-validated seams:

- `livedata.feed.MarketDataFeed` for finalized 1-minute bars;
- `execution.broker_protocol.Broker` for order, fill, position, and
  account truth.

The adapter must preserve the project’s safety posture: deterministic
tests first, paper/demo before live, broker state authoritative, and
halt rather than guess.

## Official API Surface Used

Verified from the official Tradovate API documentation at
`https://api.tradovate.com` on 2026-07-07.

Relevant surfaces:

- REST base URLs:
  - demo: `https://demo.tradovateapi.com/v1`
  - live: `https://live.tradovateapi.com/v1`
- token auth via `/auth/accesstokenrequest`
- token renewal via `/auth/renewAccessToken`
- REST requests authenticated with `Authorization: Bearer <token>`
- account endpoints such as `/account/list`, `/account/find`
- order endpoints including `/order/placeorder`, `/order/cancelorder`,
  `/order/modifyorder`, `/order/placeoco`, `/order/liquidateposition`
- position/fill endpoints including `/position/list`, `/position/deps`,
  `/fill/list`
- WebSocket user sync via `user/syncrequest`
- market-data chart subscription via WebSocket endpoint `md/getChart`
- chart subscription cancellation via `md/cancelChart`
- chart data messages of event type `chart`, containing bars with
  `timestamp`, OHLC, and volume fields
- request/connection limits, including a one-connection default unless
  the account has additional connection allowance

Design consequence: the adapter should be connection-frugal. It must
not spawn unrelated socket clients for market data, user data, and order
state without an explicit reason.

## Goals

Build a Tradovate adapter that can run in demo/paper-reconciliation mode
first and only later be allowed to place live orders under an explicit
operator gate.

It must:

- authenticate and renew tokens without storing secrets in the repo;
- subscribe to finalized 1-minute bars for the front NQ/MNQ contract;
- convert Tradovate chart bars to `VendorBar`;
- place, cancel, and flatten orders through Tradovate when enabled;
- map Tradovate order/fill updates into the existing `BrokerEvent`
  model;
- reconcile adapter-derived position truth against broker state;
- halt on unknown, partial, duplicated, or contradictory broker state;
- be testable offline with fake HTTP and WebSocket transports;
- keep all live-order behavior disabled by default.

## Non-Goals

This sub-project does not:

- change strategy logic or production config;
- implement new research sweeps;
- change the `LiveLoop` sequencing;
- add dashboards or the 30-session pilot workflow;
- solve prop-firm policy questions;
- support discretionary/manual order entry;
- support multiple simultaneous strategies or pyramiding beyond what the
  current strategy and state machine already model;
- silently recover from uncertain broker state.

If the adapter cannot prove account state, it halts.

## Chosen Approach

Use a staged adapter with injectable transports.

The first implementation builds the Tradovate domain model, auth client,
REST client, WebSocket framing parser, market-data feed, broker adapter,
and failure matrix against fake transports. The real network transport
is a small final layer that can be exercised in demo mode only after the
offline tests pass.

This is preferred over a market-data-only adapter because order-state
semantics are the riskiest part of the project and need to be designed
now. It is preferred over direct full-live wiring because the first
implementation must prove safety behavior without credentials or market
hours.

## Package Structure

Create `src/full_python/tradovate/`.

### `tradovate/config.py`

Defines static configuration and environment loading.

Core types:

```python
@dataclass(frozen=True)
class TradovateEnvironment:
    name: Literal["demo", "live"]
    rest_base_url: str
    ws_base_url: str
    md_ws_base_url: str

@dataclass(frozen=True)
class TradovateCredentials:
    username: str
    password: str
    app_id: str
    app_version: str
    client_id: int
    secret: str
    device_id: str | None = None

@dataclass(frozen=True)
class TradovateAdapterConfig:
    environment: TradovateEnvironment
    account_spec: str
    account_id: int
    root_symbol: str = "NQ"
    order_enabled: bool = False
    flatten_enabled: bool = False
    websocket_timeout_seconds: float = 10.0
    token_renewal_lead_seconds: int = 15 * 60
```

`order_enabled` and `flatten_enabled` default to `False`. A demo broker
can be connected and can observe account state with order placement
disabled. Live order routing requires explicit config.

Environment variables are allowed for credentials, but no `.env` file is
required and no secrets are committed.

### `tradovate/auth.py`

Owns access-token lifecycle.

Responsibilities:

- build the `/auth/accesstokenrequest` payload;
- parse `accessToken`, `mdAccessToken`, `userId`, and expiration fields;
- renew tokens before expiry;
- expose separate trading and market-data authorization headers/tokens;
- surface time-penalty/rate-limit responses as typed errors rather than
  retrying aggressively.

No strategy or broker logic lives here.

### `tradovate/http.py`

A tiny REST client over an injected HTTP transport.

Responsibilities:

- add Bearer auth;
- serialize JSON bodies;
- parse JSON responses;
- map non-2xx responses and Tradovate command failures into typed
  exceptions;
- provide narrow methods for the endpoints this project uses:
  - `account_list`
  - `account_find`
  - `contract_find`
  - `contract_item`
  - `contract_items`
  - `order_place`
  - `order_place_oco`
  - `order_cancel`
  - `order_modify`
  - `order_liquidate_position`
  - `position_list` / `position_deps`
  - `fill_list`

The real HTTP transport can use Python stdlib `urllib.request` to avoid
new dependency risk. Tests use an in-memory fake transport.

### `tradovate/ws.py`

WebSocket request/response and event framing over an injected transport.

Responsibilities:

- authorize a socket with the appropriate token;
- send endpoint requests such as `user/syncrequest` and `md/getChart`;
- correlate request ids to responses;
- expose async or blocking `receive()` primitives to higher-level
  adapters;
- parse event messages into typed raw event records;
- handle heartbeats and disconnects;
- close/cancel subscriptions cleanly.

Python stdlib has no WebSocket client. The implementation plan should
introduce a single lightweight dependency only at this boundary, likely
`websockets`, while all domain tests run against a fake transport.

### `tradovate/feed.py`

Implements `MarketDataFeed`.

Responsibilities:

- call `md/getChart` for `MinuteBar` with `elementSize=1` and
  `elementSizeUnit="UnderlyingUnits"`;
- store `historicalId` and `realtimeId`;
- convert incoming chart bars to `VendorBar`;
- combine `upVolume` and `downVolume` when a single volume field is not
  present;
- return only finalized minute bars;
- deduplicate bars by timestamp;
- expose `cancel()` to call `md/cancelChart` on shutdown.

`LiveBarSource` remains responsible for contract authority, timestamp
monotonicity, and outage detection. The Tradovate feed only satisfies
the vendor seam.

### `tradovate/broker.py`

Implements `execution.broker_protocol.Broker`.

Responsibilities:

- process the existing per-bar live-loop calls without changing
  `LiveLoop`;
- translate `OrderIntent.market_entry` into Tradovate
  `/order/placeorder` market orders with `isAutomated=true`;
- after an entry fill, submit broker-held protective orders from the
  intent metadata:
  - `stop_price` is required for every live-enabled entry;
  - `target_price` is optional;
  - stop-only protection uses a broker-held stop order in the opposite
    direction;
  - stop+target protection uses `order/placeoco` when available so the
    filled leg cancels the other;
  - if protection cannot be confirmed, the broker must request flatten
    and surface a fatal state error;
- map order acknowledgements to `Acked`;
- map fills to `Filled`;
- map rejects to `Rejected`;
- map cancellations to `Canceled`;
- map partial fills to `PartialFilled`;
- expose `position` from broker/user-sync truth;
- expose closed `trades` reconstructed from broker fills using the same
  field semantics as `models.Trade`;
- implement `flatten(bar, reason)` by cancelling protective/open orders
  for the active contract and then using
  `/order/liquidateposition` for the account/contract;
- refuse to place or flatten orders when the relevant enable flag is
  disabled.

Initial partial-fill policy:

> Partial fills remain fatal. The existing `OrderStateMachine` raises
> `ExecutionInvariantError` on `PartialFilled`, and `LiveLoop` halts.
> This is intentional until a later spec defines true partial-fill
> semantics.

### `tradovate/errors.py`

Typed exceptions:

- `TradovateError`
- `TradovateAuthError`
- `TradovateRateLimitError`
- `TradovateRequestError`
- `TradovateWebSocketError`
- `TradovateOrderRejected`
- `TradovateStateError`

Network, auth, and state errors must be distinguishable in logs and
tests.

## Data Flow

### Market Data

```text
Tradovate md WebSocket
  -> md/getChart MinuteBar subscription
  -> chart event bars
  -> TradovateMarketDataFeed.next_bar()
  -> VendorBar
  -> LiveBarSource
  -> MarketBar
  -> LiveLoop
```

The feed does not decide whether a contract is front-month. That remains
`ContractAuthority` inside `LiveBarSource`.

### Orders And Broker Events

```text
strategy.on_bar()
  -> OrderIntent
  -> LiveLoop
  -> TradovateBroker.apply_strategy_result()
  -> order/placeorder or flatten operation
  -> user WebSocket / REST reconciliation
  -> BrokerEvent
  -> OrderStateMachine
  -> LiveLoop cross-check
```

The broker is authoritative for account state. The strategy never
assumes an order filled because it was submitted.

## State And Reconciliation

The adapter must maintain three related views:

1. submitted order map: client/local id to Tradovate order id;
2. broker event stream: orders, fills, cancels, rejects;
3. current broker position: side, quantity, average/entry price.

Every `poll_events()` drains buffered broker events and updates the
existing state machine. `LiveLoop._cross_check()` then compares the
state-machine position to `broker.position`.

Mismatch means halt. The adapter should not “repair” position state
inside a live session unless a later operational spec defines a
supervised recovery protocol.

## Live-Order Gates

There are three levels:

1. **Offline tests:** fake transports only, no credentials, no network.
2. **Demo observe/paper:** real auth and market/user data allowed,
   `order_enabled=False`, `flatten_enabled=False`.
3. **Demo order test:** demo environment only, tiny quantity, explicit
   `order_enabled=True`, `flatten_enabled=True`.

Live environment order routing is out of scope for the first
implementation pass. The adapter may include configuration values for
live URLs, but live order enablement must remain blocked until the
Gate 5-7 operational tooling defines the checklist and pilot procedure.

## Failure Matrix

The implementation plan must include offline tests for:

- auth success;
- auth failure;
- token renewal before expiry;
- rate-limit/time-penalty response;
- WebSocket authorization success/failure;
- WebSocket disconnect before order acknowledgement;
- market-data chart subscription success;
- malformed chart data;
- duplicate chart bar timestamp;
- chart subscription cancel;
- order placement disabled by config;
- market order placement success;
- live-enabled order without `stop_price` rejected before submission;
- protective stop submitted after entry fill;
- stop+target OCO submitted after entry fill when `target_price` exists;
- protective-order confirmation failure causes fatal state error;
- order rejection;
- order cancellation;
- duplicate fill for the same order id;
- fill for unknown order id;
- partial fill event;
- broker position exists but state machine is flat;
- state machine has position but broker is flat;
- flatten disabled by config;
- flatten requested while flat;
- flatten requested while long;
- flatten requested while short;
- REST position snapshot disagreement with WebSocket state.

Any failure that leaves position truth unknown must produce a halt path,
not a retry loop that continues trading.

## Testing Strategy

All first-pass tests are offline.

Use fake transports:

- `FakeHttpTransport` with request assertions and scripted responses;
- `FakeWebSocketTransport` with scripted inbound frames and captured
  outbound frames.

Unit tests cover auth, REST request construction, WebSocket framing,
chart parsing, feed conversion, broker event mapping, and flatten
request construction.

Integration tests wire:

- `TradovateMarketDataFeed` into `LiveBarSource` with a fake clock;
- `TradovateBroker` into `LiveLoop` with fake broker events;
- `OrderStateMachine` invariant failures through the existing
  `LiveLoop` halt behavior.

No test requires real credentials.

## Dependency Policy

Do not add broad trading frameworks.

HTTP can use stdlib initially. Real WebSocket support requires one
focused dependency because Python stdlib does not provide a WebSocket
client. The implementation plan should add a single dependency at the
transport boundary and keep domain logic independent of that dependency.

Candidate dependency: `websockets`.

## Security

- Credentials are loaded from environment variables or an operator-owned
  local config path excluded from git.
- Tokens are never written to run reports, event ledgers, or exceptions.
- Logs may include endpoint names and Tradovate numeric ids, but not
  passwords, secrets, or access tokens.
- Live-order flags default to disabled.
- The adapter must make it difficult to accidentally use live URLs with
  order placement enabled.

## Amendment 2026-07-10 — Tracked Risk-Management Gaps in `TradovateBroker`

A first full review of Tasks 1-5 (the offline transport layer, plus the
broker "safety skeleton" of Task 5) found that six safety-relevant
behaviors were implemented as silent stubs, undocumented anywhere near
the code — a real risk in its own right, independent of whether the
stubs matter today (they don't: `order_enabled`/`flatten_enabled` default
`False` and no live adapter is wired into `LiveLoop`, so nothing can
trade). Recorded here, dated, so a future implementer enabling order
routing cannot miss them; each is also flagged in-line in
`tradovate/broker.py`'s module docstring and at its exact call site:

1. `daily_limit_hit` never updates (always `False`) — the strategy's own
   validated $1,000 DLL veto cannot fire live.
2. `process_bar_open` always returns `0.0` — the projected-risk
   position-sizing guard never shrinks with intraday losses.
3. `trades` always returns `[]` — `RiskSupervisor`'s daily-loss backstop
   only ever sees the open position's unrealized P&L, never realized
   losses from closed trades within a session.
4. No protective stop or OCO is ever submitted after an entry fill,
   despite `stop_price` being validated at submission time — an
   `order_enabled=True` entry currently fills naked.
5. `apply_strategy_result` processes only `result.order_intents`;
   `result.exits` and `result.stop_updates` are silently dropped — there
   is no path for the strategy to close a position through this broker.
6. `ingest_raw_event` has no submitted-order-id map (the "submitted order
   map: client/local id to Tradovate order id" this spec's State And
   Reconciliation section calls for does not exist yet). A fill for an
   order id this broker never submitted is indistinguishable from a real
   one and is applied as a genuine position update — which also silently
   defeats `LiveLoop._cross_check()`'s divergence detection, since
   position state stays in lockstep with the phantom fill.

**Binding: none of `order_enabled=True` / `flatten_enabled=True` may be
used against a funded account until all six are closed and each has a
Failure Matrix test proving it.** The review also found the Failure
Matrix (this spec's own acceptance bar) is at 12 of 28 scenarios covered,
concentrated almost entirely in the protective-order and order-lifecycle
items above — expected, since Task 6 (integration/regression) was not
yet done at review time, but recorded so the remaining count is explicit
rather than assumed.

**Closure (2026-07-10, same day):** all six gaps were closed by the
gap-closure sub-spec (`2026-07-10-tradovate-gap-closure-design.md`) —
fill-derived ledger accounting (gaps 1-3), broker-held frozen protective
stop with flatten-and-halt on confirmation failure (gap 4), the
cancel-then-close exit path (gap 5), and the submitted-order map with
halt-on-unknown/duplicate events (gap 6). The Failure Matrix stands at
28/28: 27 rows test-covered, and the stop+target OCO row recorded
N/A-by-design (the production strategy emits no `target_price`). The
binding above is amended accordingly: `order_enabled=True` /
`flatten_enabled=True` remain forbidden against a funded account until
the sub-project 4 gates (demo observe → demo order test → pilot
checklist) are passed — the gap list itself is no longer the blocker.

**Safety amendment (2026-07-12):** the 28/28 statement above is superseded by
the adversarial Phase 0 Failure Matrix V2 in
`docs/decisions/2026-07-12-phase0-correctness-remediation.md`. The original
matrix treated REST cancel acceptance as final cancellation and did not cover
the stop-fill race, exit rejection after stop cancellation, unsolicited stop
cancellation, latent entry cancellation while flat, or placement responses
without `orderId`. Those paths are now test-covered. This still does not
authorize orders: persistent client IDs, restart reconciliation, account-event
pumping, and multi-contract partial-fill semantics remain required before the
demo order phase.

Separately, an open question this spec never poses: **does Tradovate
enforce a daily-loss limit at the account/platform level** (some prop-
firm risk add-ons do), and if so, does that supplement or substitute for
gap #1/#2 above? Even if it does, account-level enforcement typically
blocks *new* order submission on breach — it does not force-flatten an
*existing* position the way the strategy's own DLL veto is specified to.
This is now an explicit open operational decision (added below), not an
assumption embedded in the code.

## Open Operational Decisions

These are intentionally not solved in this spec:

- exact Tradovate account to use;
- demo credential management;
- whether production pilot trades NQ or MNQ;
- live daily supervisor cap;
- how manual broker intervention is recorded;
- whether partial fills get modeled or remain fatal permanently;
- whether Tradovate/the prop firm enforces an account-level daily-loss
  limit that supplements or substitutes for the client-side DLL in
  Amendment 2026-07-10 gap #1/#2 above — and if it auto-liquidates open
  positions on breach or only blocks new orders.

Those belong in sub-project 4 and the pilot checklist.

## Acceptance Criteria

This sub-project is done when:

- all offline adapter tests pass;
- the adapter implements `MarketDataFeed`;
- the adapter implements `Broker`;
- live-order flags default to disabled;
- fake-transport integration proves broker events drive
  `OrderStateMachine` correctly;
- fake-transport integration proves unknown/partial/duplicate broker
  state halts rather than guessing;
- demo credentials can be supplied externally for a manual smoke test,
  but tests do not require them;
- `python3 -m pytest -q` remains green.

It is **not** done merely because an HTTP order request can be sent.
The safety and reconciliation behavior is the core deliverable.
