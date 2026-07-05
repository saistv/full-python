# Execution Core (Live-Engine Sub-Project 1) — Design

## Context

First of four live-engine sub-projects (approved decomposition:
1. execution core, 2. live data feed + contract authority, 3. Tradovate
adapter + failure matrix, 4. Gates 5-7 operational tooling). The
promotion path replay→shadow→paper→limited-live exists as protocol;
this sub-project builds the broker-agnostic machinery it runs on.

Everything here is offline-testable. No external dependencies, no
network I/O, no broker credentials. The Tradovate adapter (sub-project
3) later implements the `Broker` protocol defined here; the live feed
(sub-project 2) later implements the `BarSource` protocol defined here.

## Goal

A single-threaded, bar-clocked live execution loop that drives the
existing `AdaptiveTrendStrategy` through a broker abstraction, with an
independent account-level risk supervisor — proven by the acceptance
property that **LiveLoop + PaperBroker over recorded bars produces a
trade list identical to `SimulationEngine` on the same bars.** That
identity is what makes every later gate (paper, reconciliation, pilot)
meaningful: any live-vs-sim divergence is then attributable to the
world (data, broker, latency), never to the machinery.

## Architecture

New package `src/full_python/execution/` — six modules, each with one
responsibility. Approved Approach A: synchronous bar-driven core;
brokers sit behind a *polled* event interface so even an async adapter
later buffers into deterministic per-bar polling. Rejected
alternatives, recorded: reusing `SimulationEngine` as the live engine
(entangles broker I/O with the deterministic research tool) and an
asyncio core (nondeterminism would make the identity property
untestable; the strategy decides once per minute — YAGNI).

### 1. `execution/limits.py` — RiskLimits extraction (the flagged debt, fixed first)

Frozen `RiskLimits` dataclass carrying exactly the fields
`risk/risk_manager.py` reads from `SimulationConfig` today (max
contracts, RTH-entries-only, flatten hour/minute, daily-loss-limit
fields). `RiskManager.__init__(limits: RiskLimits)` replaces
`__init__(config: SimulationConfig)`; `SimulationEngine` constructs a
`RiskLimits` from its config at init. Behavior-preserving by
construction — the full suite (155 passed, 2 skipped) and, where data
exists, the golden-trade tests prove it unchanged. After this task,
`grep SimulationConfig src/full_python/risk/` returns nothing.

### 2. `execution/broker_protocol.py` — the broker abstraction

```python
class BrokerEvent:            # tagged union via small frozen dataclasses
    Acked(order_id)
    Filled(order_id, price, quantity, timestamp_utc)
    PartialFilled(order_id, price, quantity, remaining)
    Rejected(order_id, reason)
    Canceled(order_id)

class Broker(Protocol):
    def submit(self, intent: OrderIntent) -> str: ...   # returns broker order id
    def cancel(self, order_id: str) -> None: ...
    def on_bar(self, bar: MarketBar) -> None: ...       # market clock: paper matures
                                                        # fills here; real adapters
                                                        # may use it for marks (no-op ok)
    def poll_events(self) -> list[BrokerEvent]: ...     # drained once per bar
```

`PartialFilled` exists because Tradovate can produce it; the paper
broker never emits it, and the state machine's initial handling is
"treat as fatal halt" until the Tradovate sub-project defines real
semantics — expressible now, deferred deliberately.

### 3. `execution/state_machine.py` — order lifecycle and position truth

Pure, no I/O. Tracks orders through
`PENDING_SUBMIT → SUBMITTED → ACKED → FILLED | REJECTED | CANCELED`
and derives position (side, quantity, average price) from fills.
Invariants are enforced as exceptions, not warnings: a fill for an
unknown order id, a second fill for a filled order, a transition that
skips states — all raise `ExecutionInvariantError`. In the live loop
that exception means flatten-and-halt; in tests it means the bug is
loud. The most heavily unit-tested module.

### 4. `execution/paper_broker.py` — simulated fills, frozen policy

Implements `Broker` over bars: the loop hands it each bar
(`on_bar(bar)`); a market order submitted during bar N fills at bar
N+1's open ± the frozen slippage, with the frozen commission — the
identical fill policy `SimulationConfig`/`FROZEN_SIMULATION_OVERRIDES`
gives the sim, sourced from the same config object (never re-typed).
Fill events surface on the next `poll_events()`. On recorded data this
reproduces `SimulationEngine` fills exactly, by construction.

### 5. `execution/supervisor.py` — account-level hard limits (defense-in-depth)

`RiskSupervisor(config: RiskSupervisorConfig)` with:

- `daily_loss_stop: float` — session realized + unrealized P&L floor
  (Gate 7's $150/day becomes config, not discipline)
- `max_position_contracts: int` — absolute cap regardless of strategy
  sizing
- `kill_switch_path: Optional[Path]` — if the file exists, no new
  orders, flatten at next bar

Checked before every submit and after every fill. Breach → emit a
ledger event, flatten any open position, refuse all further entries
for the session. The supervisor is INDEPENDENT of the strategy's own
DLL: the strategy's $1K DLL is edge logic (part of the validated
config); the supervisor is an account guard that must hold even if
strategy state is corrupted. It consults only fills and marks, never
strategy internals.

### 6. `execution/live_loop.py` — the conductor

```python
class BarSource(Protocol):
    def __iter__(self) -> Iterator[MarketBar]: ...

class RecordedBarSource:   # wraps a list/CSV of bars (Gate-5-on-recorded, tests)
```

`LiveLoop(bar_source, strategy, broker, risk_manager, supervisor,
ledger)` — per bar:

1. `broker.on_bar(bar)` — the market clock tick (paper broker matures
   pending orders at this bar's open here)
2. `broker.poll_events()` → state machine updates → strategy
   `on_fill`/`on_trade_closed` hooks
3. supervisor post-fill check with this bar as the mark (breach →
   flatten + halt)
4. `strategy.on_bar(bar)` → order intents
5. per intent: supervisor pre-submit check → `RiskManager.veto_reason`
   → `broker.submit`
6. everything written to the same `EventLedger` the sim uses

**The exact processing order within a bar is NOT a design-time
invention: the implementation plan pins it by reading
`SimulationEngine.run`'s actual sequence (fills-before-strategy,
exit-before-entry handling, flatten timing) and the hook-ordering test
enforces the match.** The numbered steps above are the intended shape;
where they conflict with the sim's real order, the sim's order wins —
identity is the requirement, not this sketch.

Flatten-time handling mirrors the sim (backstop 15:59 exits via the
same session rules the RiskManager already encodes). The loop is
synchronous and owns no clock: time IS the bar stream, which is what
makes recorded-vs-live behavior comparable.

## Error-handling philosophy

Live code never guesses. Unexpected broker events, invariant
violations, or supervisor breaches: flatten if possible, halt intake,
write the full context to the ledger. No retry loops in this
sub-project (reconnect/recovery is sub-project 3's problem, where a
real network exists).

## Acceptance property (the point of the sub-project)

`LiveLoop(RecordedBarSource(bars), strategy, PaperBroker(frozen policy),
...)` must produce a trade list **identical** to
`SimulationEngine.run(bars, strategy)` — same entries, exits, prices,
quantities, P&L, in order.

- Unit level: synthetic multi-session bar fixtures where both paths
  produce a nonzero number of trades, compared field-by-field.
- Real-data level: identity over the frozen 9-month Baseline Anchor
  window (2025-10-01 → 2026-06-26), skipped when the gitignored
  dataset is absent — same pattern as the existing golden-trade tests.

Divergence of any field in any trade fails the build. This test is the
contract every later sub-project builds on.

## Testing (beyond the acceptance property)

- `state_machine`: every legal transition, every illegal transition
  raises, position math from fill sequences (including multi-contract
  AM fills).
- `paper_broker`: fill price = next bar open ± slippage for both
  sides; commission; no fill until next bar; cancel before fill.
- `supervisor`: loss-stop breach flattens and blocks; kill-switch file
  honored; max-position veto; independence from strategy state
  (supervisor decisions computed from fills alone).
- `limits`: RiskManager behavior byte-identical pre/post extraction
  (full suite green is the proof).
- `live_loop`: hook ordering vs the sim (a scripted strategy records
  its callback sequence under both engines; sequences must match).

## Explicitly out of scope (later sub-projects)

- Live data feed, bar building from ticks, contract authority, roll
  handling (sub-project 2).
- Tradovate adapter, auth, reconnect/recovery, the Broker Failure Test
  Matrix (sub-project 3).
- Gate 5-7 operational tooling, daily reconciliation reports, go/no-go
  checklists (sub-project 4).
- Order types beyond market orders — the strategy uses market entries
  and market/stop-style exits at bar granularity today; limit-order
  support is added when something needs it.
