# Tradovate Broker Gap Closure — Design

Closes the six tracked risk-management gaps recorded in the 2026-07-10
amendment to `2026-07-07-tradovate-adapter-design.md`, and completes the
Broker Failure Test Matrix (12/28 → 28/28). This is the remainder of
live-engine sub-project 3. The parent spec's binding stands: none of
`order_enabled=True` / `flatten_enabled=True` may be used against a
funded account until all six gaps are closed and each has a Failure
Matrix test proving it.

## Scope

**Offline only** (operator decision, 2026-07-10). All work runs against
fake REST/WebSocket transports; no credentials, no network, no market
hours. Gate level 2 (demo observe) and level 3 (demo order test) are the
first steps of sub-project 4, not this spec.

## Operational Facts Established 2026-07-10

- **Platform DLL behavior (operator, from live Tradeify/Tradovate
  experience): breach of the platform-level daily-loss limit
  AUTO-LIQUIDATES open positions** and blocks new orders. The platform
  is therefore a true third defense layer, ordered: strategy DLL
  ($1,000, fires first) → RiskSupervisor cap (above $1,000, per
  instrument) → platform cap (highest). Client-side gaps #1/#2 still
  close in full — the platform threshold is the prop firm's number, not
  ours, and sits far above $1,000.
- Design consequence: a platform-initiated liquidation reaches the
  adapter as a fill for an order id we never submitted. Under the gap #6
  fix that is exactly the unknown-order-id case → halt for human
  review, broker authoritative. No special-casing needed or wanted.

## Chosen Approach: Fill-Derived Ledger

`PaperBroker` answers `process_bar_open` / `trades` /
`daily_limit_hit` by wrapping the shared `PositionEngine` — a
simulation. A real broker adapter must answer from **broker truth**
instead: real fills, real position, marked at real bar prices. Only the
arithmetic is shared code.

Alternatives rejected:

- **Embedded shadow `PositionEngine`** (PaperBroker pattern): its
  numbers are simulated fills; under real slippage the DLL and session
  P&L become fiction, violating the broker-authoritative posture, and it
  duplicates `OrderStateMachine`'s shadow role.
- **Per-bar REST polling** (`/fill/list`, cash balance): strongest
  ground truth but burns Tradovate's request budget, adds latency inside
  the 1-minute bar loop, and bloats offline fakes. REST snapshots remain
  a periodic reconciliation cross-check (existing Failure Matrix item),
  not per-bar accounting.

## Components

No changes to `LiveLoop`, `PositionEngine`, `OrderStateMachine`,
`RiskSupervisor`, or any strategy code.

### New: `tradovate/ledger.py` — `FillPairingLedger`

Pure bookkeeping, no I/O:

- consumes each `Filled` event tagged with its role (entry vs. exit —
  the role comes from the submitted-order map, never guessed from
  direction);
- pairs entry fill → exit fill into a `models.Trade` with correct
  `session_date`, `entry/exit` price and timestamp, the frozen
  `stop_price` from the entry intent, `commission` from the adapter's
  cost config, `gross_pnl`/`net_pnl` via `dollar_point_value`;
- tracks per-session realized net P&L (session keyed by
  `SessionInfo.session_date`, resetting exactly as the sim's
  session-start reset does);
- exposes `trades` (closed, chronological) and
  `realized_session_pnl(session_date)`.

### Changed: `tradovate/broker.py` — four behaviors

**1. Submitted-order map (closes gap #6).**
`dict[order_id → SubmittedOrder(role, side, quantity, stop_price,
intent metadata)]`, with roles `entry | protective_stop | exit |
flatten`. Recorded at submission time from the REST response's
`orderId`. Rules:

- fill / cancel / reject for an **unknown order id** →
  `TradovateStateError` (LiveLoop halts; position truth unknown → no
  flatten). This is also the platform-liquidation path.
- **duplicate fill** for an already-filled order id →
  `TradovateStateError`.
- a `position` raw event that contradicts the order-map-derived
  position → `TradovateStateError` (feeds `_cross_check` semantics at
  the adapter level).

**2. Broker-held protective stop (closes gap #4).**
On an entry `Filled`: immediately submit a stop order (opposite side,
same quantity, the entry's frozen `stop_price`) via
`rest.order_place` with `orderType="Stop"`, `isAutomated=True`; record
it in the order map. If submission raises or is rejected: flatten, then
raise `TradovateStateError` — an enabled entry must never sit naked.
The stop is **never modified after placement**: the production fill
policy freezes stops at entry (`PositionEngine` logs `stop_updates`
with `applied: False` and never moves a stop; the only mutation is
cancellation when a flatten supersedes it). No OCO path is implemented:
the production strategy never emits `target_price`; the Failure
Matrix's OCO row is recorded N/A-by-design (see Testing).

**3. Exit path (closes gap #5).**
`apply_strategy_result` now processes `result.exits`: if a position is
open — cancel the working protective stop (`rest.order_cancel`), then
submit a market order on the opposite side for the full quantity,
recorded in the order map with role `exit`. `result.stop_updates`
remain deliberately unapplied, matching sim semantics exactly; the
no-op is documented in-line so it can never be re-read as an oversight.

**4. Fill-derived session P&L and DLL (closes gaps #1 and #2).**
`process_bar_open(bar, session)` returns
`ledger.realized_session_pnl(session) + unrealized`, where
`unrealized` is the broker position marked **gross at the bar close**
(`(close − entry) × direction × dollar_point_value × qty`) — the same
equity formula the sim uses (realized net + unrealized gross,
`openprofit` excluding the open trade's commission). The same call sets
`daily_limit_hit` via the shared
`full_python.risk.daily_loss.is_daily_loss_breached(session_pnl,
daily_loss_limit)`, and resets it at session rollover as the sim does.
On breach: cancel the protective stop → `flatten` → the resulting fill
pairs into a closed trade. This makes the strategy's own $1,000 DLL
veto (gap #1) and the projected-risk sizing guard (gap #2) live, since
`LiveLoop` already feeds both from these two answers.

### Changed: `tradovate/config.py`

`TradovateAdapterConfig` gains explicit risk/cost fields:

- `dollar_point_value: float` — **per-instrument, no default that can
  silently cross instruments** (NQ = 20.0, MNQ = 2.0);
- `commission_per_contract_round_trip: float`;
- `daily_loss_limit: float | None` — `None` permitted only while
  `order_enabled=False`; config validation rejects
  `order_enabled=True` with `daily_loss_limit=None`.

## Timing Semantics

The strategy signals at bar close; the adapter submits the market entry
immediately (as the skeleton already does). A market order sent at
signal-bar close fills at the next available price — the sim's
next-bar-open fill policy expressed in real time, and consistent with
the April 2026 live verification that real fills match the backtest.
Exits follow the same rule: submitted at the signal bar, filled at the
next tick. No artificial queuing to the next bar is added.

## Error Handling

Everything unknown halts; nothing retries into uncertainty.

| Condition | Response |
|---|---|
| Fill/cancel/reject, unknown order id (incl. platform liquidation) | `TradovateStateError` → halt, no flatten (position truth unknown) |
| Duplicate fill, same order id | `TradovateStateError` → halt |
| Protective stop submission fails or is rejected | flatten, then `TradovateStateError` (never naked) |
| Partial fill | unchanged: `PartialFilled` → `OrderStateMachine` raises → halt |
| Broker `position` event contradicts order-map-derived position | `TradovateStateError` → halt |
| DLL breach while `flatten_enabled=False` | `TradovateStateError` → halt (misconfiguration: live routing requires both flags) |

## Testing

All offline, fake transports, no credentials. Three layers:

1. **`FillPairingLedger` unit tests** — pairing, commission, session
   rollover, realized accumulation across multiple round-trips —
   **pinned against `PositionEngine`'s numbers** for the same fill
   sequence (the arithmetic must agree with the sim when fills are
   identical).
2. **Broker behavior tests** — every row of the error table above, plus
   protective-stop submission on entry fill, cancel-then-close ordering
   on exits, DLL breach sequence, session reset.
3. **`LiveLoop` integration** — a losing multi-round-trip session
   proves: realized losses accumulate → strategy DLL veto fires →
   supervisor `check_mark` sees real `trades` → breach flattens once
   and entries stay blocked; and the unknown-fill / duplicate-fill /
   position-contradiction paths halt through the real loop.

**Failure Matrix: 12/28 → 28/28.** The implementation plan must begin
with a row-by-row audit of the parent spec's 28-item matrix against the
existing test suite, then map every uncovered row to a named new test —
the open rows concentrate in protective-order and order-lifecycle
behavior (stop after entry fill, confirmation failure → flatten +
fatal, rejection, cancellation, duplicate fill, unknown-id fill,
position-vs-state-machine mismatches in both directions, flatten while
flat/long/short, REST-vs-WebSocket snapshot disagreement, WebSocket
disconnect before acknowledgement). The **stop+target OCO row is
recorded N/A-by-design** — the production strategy has no profit
target, so the row is closed by this documented decision rather than
silently skipped; implementing OCO would be dead code guarding
nothing.

Implemented: see the row-by-row audit table in
`docs/superpowers/plans/2026-07-10-tradovate-gap-closure.md` — 27 rows
test-covered + 1 N/A-by-design as of the closure commits.

## Acceptance Criteria

- All six gap annotations in `broker.py` are replaced by working
  behavior, and the module docstring's warning block is rewritten to
  describe the implemented safety model.
- Failure Matrix at 28/28 (27 tested + 1 recorded N/A-by-design), each
  row mapped to a named test in the plan.
- `FillPairingLedger` arithmetic pinned against `PositionEngine` for
  identical fill sequences.
- `python3 -m pytest -q` green.
- The parent spec's amendment is updated to mark the six gaps closed,
  with a pointer to this spec.
- Live-order flags still default to `False`; nothing in this
  sub-project connects to a real endpoint.

## Explicitly Out of Scope

- Real transports, demo observe, demo order test (sub-project 4).
- Partial-fill modeling (stays fatal; open decision unchanged).
- Supervisor cap values, account selection, credential management
  (sub-project 4 / pilot checklist).
- Any strategy or production-config change.
