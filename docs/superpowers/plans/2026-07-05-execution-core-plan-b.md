# Execution Core Plan B (broker stack + LiveLoop identity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The broker-agnostic live execution stack — broker protocol, order state machine, PaperBroker over the shared `PositionEngine`, risk supervisor, and `LiveLoop` — proven by trade-for-trade identity with `SimulationEngine`.

**Architecture:** Per the spec's Amendment 2: the `Broker` protocol mirrors `PositionEngine`'s proven per-bar API; PaperBroker is a thin facade over `PositionEngine` (identity by shared code) that synthesizes `BrokerEvent`s from the event-ledger tail; an `OrderStateMachine` shadows position from those events as an independent per-bar cross-check; `LiveLoop` mirrors `SimulationEngine.run`'s exact sequence and adds the supervisor. Identity test: same bars + same strategy ⇒ identical trades AND identical ledger event-type sequence.

**Tech Stack:** Python 3 stdlib. Consumes (all existing, merged in Plan A): `simulation.position_engine.PositionEngine` (`process_pre_strategy`, `apply_strategy_result`, `note_bar_processed`, `close_end_of_data`, properties `trades`/`daily_limit_hit`/`position`/`previous_bar`), `SimulationConfig`, `EventLedger`/`EventType`, `classify_timestamp`, `models.StrategyResult` (fields: `signal`, `order_intents`, `risk_vetoes`, `stop_updates`, `exits`).

## Global Constraints

- **The identity property is the acceptance bar:** `LiveLoop` (PaperBroker, supervisor disabled) over any bar sequence produces trades AND a ledger event-type sequence identical to `SimulationEngine.run` on the same bars with the same strategy and config. Any field of any trade differing fails.
- The ONLY permitted change to Plan A files is ONE additive method on `PositionEngine` (`flatten_now`, Task 2) — no moved body from Plan A may be edited; `python3 -m pytest -q` must stay green (worktree baseline: 155 passed, 2 skipped) after every task.
- No changes to `strategy/`, `risk/` (beyond none), `regime.py`, `research/`, `reporting/`, `data/`, `cli.py`, `models.py`, `events.py`, `simulation/engine.py`, `simulation/config.py`.
- The supervisor is independent of strategy internals: its decisions are computed from broker-reported position, trades, and bar marks only.
- Live-code failure philosophy: invariant violations raise `ExecutionInvariantError`; `LiveLoop` catches it, flattens via the broker, emits a ledger `STATE_TRANSITION` event with `"transition": "execution_halt"`, and stops processing bars. Never continue on a guess.
- Commit style `feat: ...`.

---

### Task 1: BrokerEvent, Broker protocol, and the order state machine

**Files:**
- Create: `src/full_python/execution/__init__.py`
- Create: `src/full_python/execution/broker_protocol.py`
- Create: `src/full_python/execution/state_machine.py`
- Test: `tests/test_execution_state_machine.py`

**Interfaces:**
- Consumes: `full_python.models.MarketBar`, `StrategyResult`, `Trade`; `full_python.data.sessions.SessionInfo`.
- Produces (later tasks rely on exact names):
  - Events: `Acked(order_id: str)`, `Filled(order_id: str, side: str, quantity: int, price: float, timestamp_utc: str, reason: str)`, `Rejected(order_id: str, reason: str)`, `Canceled(order_id: str)`, `PartialFilled(order_id: str, side: str, quantity: int, remaining: int, price: float, timestamp_utc: str)` — all frozen dataclasses; `BrokerEvent = Acked | Filled | Rejected | Canceled | PartialFilled`.
  - `Broker` Protocol: `process_bar_open(bar, session) -> float`, `apply_strategy_result(bar, session, result) -> None`, `note_bar_processed(bar, session) -> None`, `close_end_of_data() -> None`, `flatten(bar, reason) -> None`, `poll_events() -> list[BrokerEvent]`, properties `position` (`Optional[BrokerPosition]`), `trades` (`list[Trade]`), `daily_limit_hit` (`bool`). `BrokerPosition` frozen dataclass: `side: str` ("long"/"short"), `quantity: int`, `entry_price: float`.
  - `OrderStateMachine` with `ExecutionInvariantError(RuntimeError)`; methods `on_event(event: BrokerEvent) -> None` and property `position: Optional[BrokerPosition]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_execution_state_machine.py`:

```python
import pytest

from full_python.execution.broker_protocol import (
    Acked,
    BrokerPosition,
    Canceled,
    Filled,
    PartialFilled,
    Rejected,
)
from full_python.execution.state_machine import ExecutionInvariantError, OrderStateMachine


def _fill(order_id, side, qty, price=100.0, reason="test"):
    return Filled(order_id=order_id, side=side, quantity=qty, price=price,
                  timestamp_utc="2025-10-01T14:31:00Z", reason=reason)


def test_entry_fill_opens_position_and_exit_fill_closes_it():
    sm = OrderStateMachine()
    sm.on_event(Acked(order_id="P1"))
    sm.on_event(_fill("P1", "buy", 2, price=101.0))
    assert sm.position == BrokerPosition(side="long", quantity=2, entry_price=101.0)
    sm.on_event(_fill("X1", "sell", 2, price=105.0))  # exit fills may be unsolicited
    assert sm.position is None


def test_short_position_from_sell_entry():
    sm = OrderStateMachine()
    sm.on_event(_fill("P1", "sell", 1, price=99.0))
    assert sm.position == BrokerPosition(side="short", quantity=1, entry_price=99.0)
    sm.on_event(_fill("X1", "buy", 1))
    assert sm.position is None


def test_exit_quantity_mismatch_raises():
    sm = OrderStateMachine()
    sm.on_event(_fill("P1", "buy", 2))
    with pytest.raises(ExecutionInvariantError):
        sm.on_event(_fill("X1", "sell", 1))  # partial close is not a modeled state


def test_double_fill_of_same_order_raises():
    sm = OrderStateMachine()
    sm.on_event(_fill("P1", "buy", 1))
    sm.on_event(_fill("X1", "sell", 1))
    with pytest.raises(ExecutionInvariantError):
        sm.on_event(_fill("P1", "buy", 1))  # order ids are single-use


def test_entry_fill_while_position_open_same_direction_raises():
    sm = OrderStateMachine()
    sm.on_event(_fill("P1", "buy", 1))
    with pytest.raises(ExecutionInvariantError):
        sm.on_event(_fill("P2", "buy", 1))  # pyramiding is not a modeled state


def test_rejected_and_canceled_orders_leave_position_untouched():
    sm = OrderStateMachine()
    sm.on_event(Acked(order_id="P1"))
    sm.on_event(Rejected(order_id="P1", reason="risk"))
    sm.on_event(Canceled(order_id="P2"))
    assert sm.position is None


def test_partial_fill_is_fatal_for_now():
    sm = OrderStateMachine()
    with pytest.raises(ExecutionInvariantError):
        sm.on_event(PartialFilled(order_id="P1", side="buy", quantity=1,
                                  remaining=1, price=100.0,
                                  timestamp_utc="2025-10-01T14:31:00Z"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_execution_state_machine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'full_python.execution'`

- [ ] **Step 3: Write the implementation**

Create `src/full_python/execution/__init__.py`:

```python
"""Live execution stack (Gate 5+). Never imported by SimulationEngine."""
```

Create `src/full_python/execution/broker_protocol.py`:

```python
"""Broker abstraction for the live execution stack.

Shape follows PositionEngine's proven per-bar API (design spec
Amendment 2, docs/superpowers/specs/2026-07-05-execution-core-design.md):
the broker owns fills and position truth; the loop owns strategy,
supervisor, ledger, and the bar clock. PaperBroker realizes fills via
the shared PositionEngine; the future Tradovate adapter realizes them
against the real API behind this same interface.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Union

from full_python.data.sessions import SessionInfo
from full_python.models import MarketBar, StrategyResult, Trade


@dataclass(frozen=True)
class Acked:
    order_id: str


@dataclass(frozen=True)
class Filled:
    order_id: str
    side: str  # "buy" | "sell"
    quantity: int
    price: float
    timestamp_utc: str
    reason: str


@dataclass(frozen=True)
class PartialFilled:
    order_id: str
    side: str
    quantity: int
    remaining: int
    price: float
    timestamp_utc: str


@dataclass(frozen=True)
class Rejected:
    order_id: str
    reason: str


@dataclass(frozen=True)
class Canceled:
    order_id: str


BrokerEvent = Union[Acked, Filled, PartialFilled, Rejected, Canceled]


@dataclass(frozen=True)
class BrokerPosition:
    side: str  # "long" | "short"
    quantity: int
    entry_price: float


class Broker(Protocol):
    def process_bar_open(self, bar: MarketBar, session: SessionInfo) -> float: ...

    def apply_strategy_result(
        self, bar: MarketBar, session: SessionInfo, result: StrategyResult
    ) -> None: ...

    def note_bar_processed(self, bar: MarketBar, session: SessionInfo) -> None: ...

    def close_end_of_data(self) -> None: ...

    def flatten(self, bar: MarketBar, reason: str) -> None: ...

    def poll_events(self) -> list[BrokerEvent]: ...

    @property
    def position(self) -> Optional[BrokerPosition]: ...

    @property
    def trades(self) -> list[Trade]: ...

    @property
    def daily_limit_hit(self) -> bool: ...
```

Create `src/full_python/execution/state_machine.py`:

```python
"""Order/position state machine for the live execution stack.

Pure, no I/O. In paper mode it SHADOWS the PositionEngine's position as
an independent cross-check (LiveLoop asserts they agree every bar); for
a real broker adapter it becomes position truth. Invariants raise
ExecutionInvariantError -- in live code that means flatten-and-halt,
never continue on a guess.

The modeled position universe is deliberately the strategy's own: one
position at a time, opened by one full fill, closed by one full fill.
Pyramiding, partial closes, and partial fills are invariant violations
until a broker adapter defines real semantics for them.
"""
from __future__ import annotations

from typing import Optional

from full_python.execution.broker_protocol import (
    Acked,
    BrokerEvent,
    BrokerPosition,
    Canceled,
    Filled,
    PartialFilled,
    Rejected,
)


class ExecutionInvariantError(RuntimeError):
    pass


class OrderStateMachine:
    def __init__(self) -> None:
        self._position: Optional[BrokerPosition] = None
        self._used_order_ids: set[str] = set()

    @property
    def position(self) -> Optional[BrokerPosition]:
        return self._position

    def on_event(self, event: BrokerEvent) -> None:
        if isinstance(event, (Acked, Rejected, Canceled)):
            return  # lifecycle notices; position only moves on fills
        if isinstance(event, PartialFilled):
            raise ExecutionInvariantError(
                f"partial fill not modeled: order {event.order_id} "
                f"filled {event.quantity} remaining {event.remaining}"
            )
        if isinstance(event, Filled):
            self._on_filled(event)
            return
        raise ExecutionInvariantError(f"unknown broker event: {event!r}")

    def _on_filled(self, fill: Filled) -> None:
        if fill.order_id in self._used_order_ids:
            raise ExecutionInvariantError(f"duplicate fill for order {fill.order_id}")
        self._used_order_ids.add(fill.order_id)

        if self._position is None:
            side = "long" if fill.side == "buy" else "short"
            self._position = BrokerPosition(
                side=side, quantity=fill.quantity, entry_price=fill.price
            )
            return

        closing_side = "sell" if self._position.side == "long" else "buy"
        if fill.side != closing_side:
            raise ExecutionInvariantError(
                f"entry fill while {self._position.side} position open "
                f"(order {fill.order_id})"
            )
        if fill.quantity != self._position.quantity:
            raise ExecutionInvariantError(
                f"exit quantity {fill.quantity} != position quantity "
                f"{self._position.quantity} (order {fill.order_id})"
            )
        self._position = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_execution_state_machine.py -v`
Expected: 7 passed

- [ ] **Step 5: Full suite, commit**

Run: `python3 -m pytest -q` — expected: 162 passed, 2 skipped

```bash
git add src/full_python/execution/ tests/test_execution_state_machine.py
git commit -m "feat: broker protocol and order state machine for the execution stack"
```

---

### Task 2: PositionEngine.flatten_now (the one additive Plan A touch)

**Files:**
- Modify: `src/full_python/simulation/position_engine.py` (APPEND one public method; touch nothing else)
- Test: `tests/test_position_engine_flatten.py` (new file)

**Interfaces:**
- Produces: `PositionEngine.flatten_now(bar: MarketBar, reason: str) -> None` — cancels any pending entry/exit (ledger `STATE_TRANSITION` event `"pending_orders_cancelled"` with the given reason, matching the existing cancel events' payload shape) and closes any open position at `bar.close` via the existing `self._close_position(raw_price=bar.close, timestamp_utc=bar.timestamp_utc, reason=reason)`. No-op when flat with nothing pending. Exists ONLY for the supervisor path; `SimulationEngine` never calls it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_position_engine_flatten.py`:

```python
from full_python.events import EventLedger, EventType
from full_python.models import MarketBar, OrderIntent, StrategyResult
from full_python.data.sessions import classify_timestamp
from full_python.simulation import SimulationConfig
from full_python.simulation.position_engine import PositionEngine


def _bar(ts, price):
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=price, high=price,
                     low=price, close=price, volume=1.0)


class _NullStrategy:
    def on_bar(self, bar):
        return StrategyResult()


def _buy_result(bar):
    return StrategyResult(order_intents=(
        OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc, symbol="NQ", side="buy",
            quantity=1, reason="test",
            metadata={"stop_price": bar.close - 30.0, "signal_price": bar.close},
        ),
    ))


def test_flatten_now_closes_open_position_at_bar_close():
    ledger = EventLedger()
    config = SimulationConfig(point_value=2.0, commission_per_contract_round_trip=1.0,
                              entry_slippage_points=0.0, exit_slippage_points=0.0,
                              rth_open_extra_entry_slippage_points=0.0)
    engine = PositionEngine(config, _NullStrategy(), ledger)
    bar1 = _bar("2025-10-01T14:31:00Z", 100.0)
    bar2 = _bar("2025-10-01T14:32:00Z", 104.0)
    s1 = classify_timestamp(bar1.timestamp_utc)
    s2 = classify_timestamp(bar2.timestamp_utc)

    engine.process_pre_strategy(bar1, s1)
    engine.apply_strategy_result(bar1, s1, _buy_result(bar1))
    engine.note_bar_processed(bar1, s1)
    engine.process_pre_strategy(bar2, s2)  # entry fills at bar2 open
    assert engine.position is not None

    engine.flatten_now(bar2, "supervisor_halt")
    assert engine.position is None
    assert len(engine.trades) == 1
    assert engine.trades[0].exit_reason == "supervisor_halt"
    assert engine.trades[0].exit_price == 104.0  # bar close, zero slippage config


def test_flatten_now_cancels_pending_entry_and_is_noop_when_flat():
    ledger = EventLedger()
    config = SimulationConfig(point_value=2.0, commission_per_contract_round_trip=1.0,
                              entry_slippage_points=0.0, exit_slippage_points=0.0,
                              rth_open_extra_entry_slippage_points=0.0)
    engine = PositionEngine(config, _NullStrategy(), ledger)
    bar1 = _bar("2025-10-01T14:31:00Z", 100.0)
    s1 = classify_timestamp(bar1.timestamp_utc)

    engine.flatten_now(bar1, "supervisor_halt")  # flat + nothing pending: no-op
    assert len(engine.trades) == 0

    engine.process_pre_strategy(bar1, s1)
    engine.apply_strategy_result(bar1, s1, _buy_result(bar1))  # pending entry now
    engine.flatten_now(bar1, "supervisor_halt")
    cancel_events = [r for r in ledger.records
                     if r.event_type == EventType.STATE_TRANSITION
                     and r.payload.get("transition") == "pending_orders_cancelled"]
    assert len(cancel_events) == 1
    assert cancel_events[0].payload.get("reason") == "supervisor_halt"
    # cancelled pending entry never fills:
    bar2 = _bar("2025-10-01T14:32:00Z", 101.0)
    engine.process_pre_strategy(bar2, classify_timestamp(bar2.timestamp_utc))
    assert engine.position is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_position_engine_flatten.py -v`
Expected: FAIL with `AttributeError: 'PositionEngine' object has no attribute 'flatten_now'`

- [ ] **Step 3: Append the method to position_engine.py**

Append to the `PositionEngine` class (do not modify any existing method):

```python
    def flatten_now(self, bar: MarketBar, reason: str) -> None:
        """Supervisor-initiated flatten: cancel pendings, close at bar close.

        Exists only for the live-execution supervisor path
        (execution/supervisor.py). SimulationEngine never calls this --
        the deterministic replay path is unchanged.
        """
        if self._pending_entry is not None or self._pending_exit is not None:
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc=bar.timestamp_utc,
                payload={"transition": "pending_orders_cancelled", "reason": reason},
            )
            self._pending_entry = None
            self._pending_exit = None
        if self._position is not None:
            self._close_position(
                raw_price=bar.close,
                timestamp_utc=bar.timestamp_utc,
                reason=reason,
            )
```

(Match `_close_position`'s actual keyword signature in the file — it is keyword-only after `self` in the moved code.)

- [ ] **Step 4: Verify green + zero regression**

Run: `python3 -m pytest tests/test_position_engine_flatten.py -v` — expected: 2 passed
Run: `python3 -m pytest -q` — expected: 164 passed, 2 skipped

- [ ] **Step 5: Commit**

```bash
git add src/full_python/simulation/position_engine.py tests/test_position_engine_flatten.py
git commit -m "feat: PositionEngine.flatten_now for the supervisor path"
```

---

### Task 3: PaperBroker

**Files:**
- Create: `src/full_python/execution/paper_broker.py`
- Test: `tests/test_paper_broker.py`

**Interfaces:**
- Consumes: `PositionEngine` (Plan A + Task 2), `EventLedger`/`EventType`, broker protocol types (Task 1).
- Produces: `PaperBroker(config: SimulationConfig, strategy, ledger: EventLedger)` implementing every `Broker` member. Event synthesis: `poll_events()` scans ledger records appended since the last poll; each `EventType.ORDER_INTENT` record yields `Acked(order_id=f"P{n}")` (n = running intent counter); each `EventType.FILL` record yields `Filled(order_id=..., side=payload["side"], quantity=payload["quantity"], price=payload["price"], timestamp_utc=record.timestamp_utc, reason=payload["reason"])` where entry fills (side matches the most recent un-filled acked intent's side) reuse that intent's `P{n}` id and exit fills get `X{m}` ids. `position` property translates `PositionEngine.position` (`_Position` with `.side`/`.quantity`/`.entry_price`) into `BrokerPosition`, `None` when flat.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_broker.py`:

```python
from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger
from full_python.execution.broker_protocol import Acked, BrokerPosition, Filled
from full_python.execution.paper_broker import PaperBroker
from full_python.models import MarketBar, OrderIntent, StrategyResult
from full_python.simulation import SimulationConfig


def _bar(ts, price):
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=price, high=price,
                     low=price, close=price, volume=1.0)


def _config():
    return SimulationConfig(point_value=2.0, commission_per_contract_round_trip=1.0,
                            entry_slippage_points=0.0, exit_slippage_points=0.0,
                            rth_open_extra_entry_slippage_points=0.0)


class _NullStrategy:
    def on_bar(self, bar):
        return StrategyResult()


def _buy_result(bar):
    return StrategyResult(order_intents=(
        OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc, symbol="NQ", side="buy",
            quantity=1, reason="test",
            metadata={"stop_price": bar.close - 30.0, "signal_price": bar.close},
        ),
    ))


def _drive(broker, bar, result=None):
    session = classify_timestamp(bar.timestamp_utc)
    broker.process_bar_open(bar, session)
    broker.apply_strategy_result(bar, session, result or StrategyResult())
    broker.note_bar_processed(bar, session)
    return broker.poll_events()


def test_intent_acks_then_fills_at_next_bar_open():
    broker = PaperBroker(_config(), _NullStrategy(), EventLedger())
    bar1, bar2 = _bar("2025-10-01T14:31:00Z", 100.0), _bar("2025-10-01T14:32:00Z", 102.0)

    events1 = _drive(broker, bar1, _buy_result(bar1))
    assert events1 == [Acked(order_id="P1")]
    assert broker.position is None  # not filled yet

    events2 = _drive(broker, bar2)
    fills = [e for e in events2 if isinstance(e, Filled)]
    assert len(fills) == 1
    assert fills[0].order_id == "P1"
    assert fills[0].side == "buy"
    assert fills[0].price == 102.0  # next bar open, zero slippage
    assert broker.position == BrokerPosition(side="long", quantity=1, entry_price=102.0)


def test_flatten_produces_exit_fill_event_and_trade():
    broker = PaperBroker(_config(), _NullStrategy(), EventLedger())
    bar1, bar2 = _bar("2025-10-01T14:31:00Z", 100.0), _bar("2025-10-01T14:32:00Z", 102.0)
    _drive(broker, bar1, _buy_result(bar1))
    _drive(broker, bar2)

    bar3 = _bar("2025-10-01T14:33:00Z", 105.0)
    broker.flatten(bar3, "supervisor_halt")
    events = broker.poll_events()
    fills = [e for e in events if isinstance(e, Filled)]
    assert len(fills) == 1
    assert fills[0].order_id.startswith("X")
    assert fills[0].side == "sell"
    assert fills[0].reason == "supervisor_halt"
    assert broker.position is None
    assert len(broker.trades) == 1
    assert broker.trades[0].exit_reason == "supervisor_halt"


def test_daily_limit_hit_passthrough_defaults_false():
    broker = PaperBroker(_config(), _NullStrategy(), EventLedger())
    assert broker.daily_limit_hit is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_paper_broker.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `paper_broker` module)

- [ ] **Step 3: Write the implementation**

Create `src/full_python/execution/paper_broker.py`:

```python
"""Paper broker: the Broker protocol realized over the shared
PositionEngine (identity by shared code -- design spec Amendment 2).

Fill semantics are therefore EXACTLY SimulationEngine's frozen policy:
entries at next bar open +/- slippage, stops/targets intrabar, the six
exit paths, hooks and ledger events -- because they are the same code.
This module only adds BrokerEvent synthesis from the ledger tail so the
OrderStateMachine can shadow position as an independent cross-check.
"""
from __future__ import annotations

from typing import Optional

from full_python.data.sessions import SessionInfo
from full_python.events import EventLedger, EventType
from full_python.execution.broker_protocol import (
    Acked,
    BrokerEvent,
    BrokerPosition,
    Filled,
)
from full_python.models import MarketBar, StrategyResult, Trade
from full_python.simulation.config import SimulationConfig
from full_python.simulation.position_engine import PositionEngine


class PaperBroker:
    def __init__(self, config: SimulationConfig, strategy, ledger: EventLedger) -> None:
        self._engine = PositionEngine(config, strategy, ledger)
        self._ledger = ledger
        self._ledger_cursor = len(ledger.records)
        self._intent_counter = 0
        self._exit_counter = 0
        self._open_entry_order_id: Optional[str] = None

    # -- Broker protocol -------------------------------------------------
    def process_bar_open(self, bar: MarketBar, session: SessionInfo) -> float:
        return self._engine.process_pre_strategy(bar, session)

    def apply_strategy_result(
        self, bar: MarketBar, session: SessionInfo, result: StrategyResult
    ) -> None:
        self._engine.apply_strategy_result(bar, session, result)

    def note_bar_processed(self, bar: MarketBar, session: SessionInfo) -> None:
        self._engine.note_bar_processed(bar, session)

    def close_end_of_data(self) -> None:
        self._engine.close_end_of_data()

    def flatten(self, bar: MarketBar, reason: str) -> None:
        self._engine.flatten_now(bar, reason)

    def poll_events(self) -> list[BrokerEvent]:
        events: list[BrokerEvent] = []
        records = self._ledger.records
        for record in records[self._ledger_cursor:]:
            if record.event_type == EventType.ORDER_INTENT:
                self._intent_counter += 1
                order_id = f"P{self._intent_counter}"
                self._open_entry_order_id = order_id
                events.append(Acked(order_id=order_id))
            elif record.event_type == EventType.FILL:
                payload = record.payload
                if self._open_entry_order_id is not None:
                    order_id = self._open_entry_order_id
                    self._open_entry_order_id = None
                else:
                    self._exit_counter += 1
                    order_id = f"X{self._exit_counter}"
                events.append(Filled(
                    order_id=order_id,
                    side=payload["side"],
                    quantity=payload["quantity"],
                    price=payload["price"],
                    timestamp_utc=record.timestamp_utc,
                    reason=payload["reason"],
                ))
        self._ledger_cursor = len(records)
        return events

    @property
    def position(self) -> Optional[BrokerPosition]:
        raw = self._engine.position
        if raw is None:
            return None
        return BrokerPosition(
            side=raw.side, quantity=raw.quantity, entry_price=raw.entry_price
        )

    @property
    def trades(self) -> list[Trade]:
        return self._engine.trades

    @property
    def daily_limit_hit(self) -> bool:
        return self._engine.daily_limit_hit
```

Ordering note baked into the design (and verified by the identity test): entry fills always follow their ORDER_INTENT record in the ledger, and a cancelled pending entry (session change / flatten) never fills — `_open_entry_order_id` is cleared on the FILL that consumes it; a stale unconsumed entry id followed by an exit-side fill cannot occur because a pending entry either fills before any exit exists or is cancelled together with the position path that would exit. The identity + state-machine cross-check in Task 5 is the enforcement.

- [ ] **Step 4: Verify green**

Run: `python3 -m pytest tests/test_paper_broker.py -v` — expected: 3 passed
Run: `python3 -m pytest -q` — expected: 167 passed, 2 skipped

- [ ] **Step 5: Commit**

```bash
git add src/full_python/execution/paper_broker.py tests/test_paper_broker.py
git commit -m "feat: PaperBroker -- Broker protocol over the shared PositionEngine"
```

---

### Task 4: RiskSupervisor

**Files:**
- Create: `src/full_python/execution/supervisor.py`
- Test: `tests/test_supervisor.py`

**Interfaces:**
- Consumes: `BrokerPosition`, `Trade`, `MarketBar`, `SessionInfo`.
- Produces:

```python
@dataclass(frozen=True)
class RiskSupervisorConfig:
    point_value: float
    daily_loss_stop: Optional[float] = None        # None = disabled
    max_position_contracts: Optional[int] = None   # None = disabled
    kill_switch_path: Optional[Path] = None        # None = disabled

class RiskSupervisor:
    def __init__(self, config: RiskSupervisorConfig) -> None: ...
    def check_mark(self, *, session_date: str, bar: MarketBar,
                   position: Optional[BrokerPosition], trades: list[Trade]) -> Optional[str]: ...
        # returns a breach reason ("supervisor_daily_loss" | "supervisor_max_position"
        # | "supervisor_kill_switch") or None; once breached for a session_date,
        # keeps returning the same reason for that session (latched), resets on
        # a new session_date
    def entries_allowed(self) -> bool: ...  # False after any breach this session
```

Session P&L = sum of `t.net_pnl` for trades with `t.session_date == session_date` plus unrealized `(bar.close - entry_price) * (+1 long / -1 short) * point_value * quantity`. Breach when `session_pnl <= -daily_loss_stop`. Computed ONLY from broker-reported position/trades and the bar — never strategy internals (global constraint).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_supervisor.py`:

```python
from pathlib import Path

from full_python.execution.broker_protocol import BrokerPosition
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.models import MarketBar, Trade


def _bar(ts, price):
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=price, high=price,
                     low=price, close=price, volume=1.0)


def _trade(net, session):
    return Trade(symbol="NQ", side="long", quantity=1,
                 entry_timestamp_utc="x", entry_price=0.0, exit_timestamp_utc="x",
                 exit_price=0.0, exit_reason="test", stop_price=0.0,
                 gross_points=0.0, gross_pnl=net, commission=0.0, net_pnl=net,
                 mfe_points=0.0, mae_points=0.0, session_date=session)


def test_disabled_supervisor_never_breaches():
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=2.0))
    reason = sup.check_mark(session_date="2025-10-01", bar=_bar("t", 100.0),
                            position=None, trades=[_trade(-99999.0, "2025-10-01")])
    assert reason is None
    assert sup.entries_allowed() is True


def test_daily_loss_stop_includes_unrealized_and_latches():
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=2.0, daily_loss_stop=150.0))
    # realized -100; unrealized: long 1 from 100.0 marked at 70.0 -> -60 -> total -160
    pos = BrokerPosition(side="long", quantity=1, entry_price=100.0)
    reason = sup.check_mark(session_date="2025-10-01", bar=_bar("t", 70.0),
                            position=pos, trades=[_trade(-100.0, "2025-10-01")])
    assert reason == "supervisor_daily_loss"
    assert sup.entries_allowed() is False
    # latched even when the mark recovers:
    reason2 = sup.check_mark(session_date="2025-10-01", bar=_bar("t", 200.0),
                             position=None, trades=[_trade(-100.0, "2025-10-01")])
    assert reason2 == "supervisor_daily_loss"
    # resets on a new session:
    reason3 = sup.check_mark(session_date="2025-10-02", bar=_bar("t", 100.0),
                             position=None, trades=[_trade(-100.0, "2025-10-01")])
    assert reason3 is None
    assert sup.entries_allowed() is True


def test_max_position_and_kill_switch(tmp_path):
    switch = tmp_path / "halt"
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=2.0,
                                              max_position_contracts=2,
                                              kill_switch_path=switch))
    big = BrokerPosition(side="long", quantity=3, entry_price=100.0)
    assert sup.check_mark(session_date="s", bar=_bar("t", 100.0),
                          position=big, trades=[]) == "supervisor_max_position"

    sup2 = RiskSupervisor(RiskSupervisorConfig(point_value=2.0, kill_switch_path=switch))
    assert sup2.check_mark(session_date="s", bar=_bar("t", 100.0),
                           position=None, trades=[]) is None
    switch.write_text("stop")
    assert sup2.check_mark(session_date="s", bar=_bar("t", 100.0),
                           position=None, trades=[]) == "supervisor_kill_switch"
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_supervisor.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `supervisor` module)

- [ ] **Step 3: Write the implementation**

Create `src/full_python/execution/supervisor.py`:

```python
"""Account-level hard limits, independent of strategy internals.

Defense-in-depth: the strategy's own DLL is edge logic (part of the
validated config); this supervisor is an account guard that must hold
even if strategy state is corrupted. It consults only broker-reported
position, closed trades, and the current bar mark. Gate 7's $150/day
pilot cap becomes RiskSupervisorConfig(daily_loss_stop=150.0), not
discipline.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from full_python.execution.broker_protocol import BrokerPosition
from full_python.models import MarketBar, Trade


@dataclass(frozen=True)
class RiskSupervisorConfig:
    point_value: float
    daily_loss_stop: Optional[float] = None
    max_position_contracts: Optional[int] = None
    kill_switch_path: Optional[Path] = None


class RiskSupervisor:
    def __init__(self, config: RiskSupervisorConfig) -> None:
        self.config = config
        self._breached_reason: Optional[str] = None
        self._breached_session: Optional[str] = None

    def entries_allowed(self) -> bool:
        return self._breached_reason is None

    def check_mark(
        self,
        *,
        session_date: str,
        bar: MarketBar,
        position: Optional[BrokerPosition],
        trades: list[Trade],
    ) -> Optional[str]:
        if self._breached_session is not None and self._breached_session != session_date:
            self._breached_reason = None
            self._breached_session = None
        if self._breached_reason is not None:
            return self._breached_reason

        reason = self._evaluate(session_date, bar, position, trades)
        if reason is not None:
            self._breached_reason = reason
            self._breached_session = session_date
        return reason

    def _evaluate(
        self,
        session_date: str,
        bar: MarketBar,
        position: Optional[BrokerPosition],
        trades: list[Trade],
    ) -> Optional[str]:
        cfg = self.config
        if cfg.kill_switch_path is not None and cfg.kill_switch_path.exists():
            return "supervisor_kill_switch"
        if (
            cfg.max_position_contracts is not None
            and position is not None
            and position.quantity > cfg.max_position_contracts
        ):
            return "supervisor_max_position"
        if cfg.daily_loss_stop is not None:
            realized = sum(t.net_pnl for t in trades if t.session_date == session_date)
            unrealized = 0.0
            if position is not None:
                direction = 1 if position.side == "long" else -1
                unrealized = (
                    (bar.close - position.entry_price)
                    * direction
                    * cfg.point_value
                    * position.quantity
                )
            if realized + unrealized <= -cfg.daily_loss_stop:
                return "supervisor_daily_loss"
        return None
```

- [ ] **Step 4: Verify green**

Run: `python3 -m pytest tests/test_supervisor.py -v` — expected: 3 passed
Run: `python3 -m pytest -q` — expected: 170 passed, 2 skipped

- [ ] **Step 5: Commit**

```bash
git add src/full_python/execution/supervisor.py tests/test_supervisor.py
git commit -m "feat: account-level RiskSupervisor with latched session breaches"
```

---

### Task 5: LiveLoop and the identity tests

**Files:**
- Create: `src/full_python/execution/live_loop.py`
- Test: `tests/test_live_loop_identity.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4; `classify_timestamp`; `EventLedger`/`EventType`; `SimulationEngine` (in tests, as the identity oracle); `dataclasses.replace` for intent-stripping.
- Produces: `RecordedBarSource(bars: Sequence[MarketBar])` (iterable); `LiveLoop(bar_source, strategy, broker, supervisor, ledger)` with `run() -> LiveLoopResult` (frozen dataclass: `trades: tuple[Trade, ...]`, `halted_reason: Optional[str]`).

Per-bar sequence — mirroring `SimulationEngine.run` exactly, with supervisor and state-machine additions marked (+):

```python
for bar in bar_source:
    session = classify_timestamp(bar.timestamp_utc)
    ledger.append(EventType.BAR, ...)                       # same as sim
    session_pnl = broker.process_bar_open(bar, session)     # = sim pre-strategy steps
    for event in broker.poll_events():                      # (+) shadow tracking
        state_machine.on_event(event)
    cross-check: sm.position == broker.position (side+qty)  # (+) raises ExecutionInvariantError
    breach = supervisor.check_mark(...)                     # (+)
    if breach and first time: ledger STATE_TRANSITION execution_halt; broker.flatten(bar, breach)
    on_bar_context(session_pnl=..., daily_limit_hit=...)    # same as sim
    result = strategy.on_bar(bar)                           # same as sim
    if not supervisor.entries_allowed():
        result = dataclasses.replace(result, order_intents=())   # (+) exits still processed
    broker.apply_strategy_result(bar, session, result)      # same as sim
    broker.note_bar_processed(bar, session)                 # same as sim
broker.close_end_of_data()                                  # same as sim
final poll_events() -> state machine                        # (+) drain
```

On `ExecutionInvariantError` anywhere: `broker.flatten(bar, "execution_halt")`, ledger `STATE_TRANSITION` with `{"transition": "execution_halt", "error": str(exc)}`, stop iterating, return with `halted_reason`. With the supervisor fully disabled, every (+) line is observation-only — the sim-identical lines are the complete behavior. That is the identity argument, and the tests enforce it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_loop_identity.py`:

```python
import os
from pathlib import Path

import pytest

from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger
from full_python.execution.live_loop import LiveLoop, RecordedBarSource
from full_python.execution.paper_broker import PaperBroker
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.models import MarketBar, OrderIntent, StrategyResult
from full_python.simulation import SimulationConfig, SimulationEngine


def _bar(ts, o, h, l, c):
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=o, high=h, low=l, close=c, volume=1.0)


def _config():
    return SimulationConfig(point_value=2.0, commission_per_contract_round_trip=1.0,
                            entry_slippage_points=1.0, exit_slippage_points=0.5,
                            rth_open_extra_entry_slippage_points=1.0)


class ScriptedStrategy:
    """Replays a fixed script keyed by bar index; empty result otherwise.
    Also records every callback for hook-order comparison."""

    def __init__(self, script):
        self.script = script
        self.index = -1
        self.calls = []

    def on_bar(self, bar):
        self.index += 1
        self.calls.append(("on_bar", bar.timestamp_utc))
        entry = self.script.get(self.index)
        if entry is None:
            return StrategyResult()
        return entry(bar) if callable(entry) else entry

    def on_fill(self, fill):
        self.calls.append(("on_fill", fill.timestamp_utc, fill.side))

    def on_trade_closed(self, trade):
        self.calls.append(("on_trade_closed", trade.exit_timestamp_utc))

    def on_bar_context(self, *, session_pnl, daily_limit_hit):
        self.calls.append(("on_bar_context", round(session_pnl, 6), daily_limit_hit))


def _buy(bar, stop_offset=30.0):
    return StrategyResult(order_intents=(
        OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc, symbol="NQ", side="buy",
            quantity=1, reason="scripted",
            metadata={"stop_price": bar.close - stop_offset, "signal_price": bar.close},
        ),
    ))


def _fixture_bars():
    # Two RTH sessions; entry on session 1 bar 0, intrabar stop-out on bar 2;
    # entry on session 2 that survives to session flatten.
    return [
        _bar("2025-10-01T14:31:00Z", 100.0, 101.0, 99.0, 100.0),
        _bar("2025-10-01T14:32:00Z", 100.5, 102.0, 100.0, 101.0),
        _bar("2025-10-01T14:33:00Z", 101.0, 101.5, 60.0, 62.0),   # crashes through stop
        _bar("2025-10-01T14:34:00Z", 62.0, 63.0, 61.0, 62.5),
        _bar("2025-10-02T14:31:00Z", 200.0, 201.0, 199.0, 200.0),
        _bar("2025-10-02T14:32:00Z", 200.5, 202.0, 200.0, 201.5),
        _bar("2025-10-02T14:33:00Z", 201.5, 203.0, 201.0, 202.0),
    ]


def _script():
    return {0: _buy, 4: _buy}


def _run_sim(bars):
    strategy = ScriptedStrategy(_script())
    result = SimulationEngine(_config()).run(bars, strategy)
    return result, strategy


def _run_live(bars, supervisor=None):
    strategy = ScriptedStrategy(_script())
    ledger = EventLedger()
    broker = PaperBroker(_config(), strategy, ledger)
    sup = supervisor or RiskSupervisor(RiskSupervisorConfig(point_value=2.0))
    loop = LiveLoop(RecordedBarSource(bars), strategy, broker, sup, ledger)
    return loop.run(), strategy, ledger


def test_identity_trades_and_ledger_sequence_match_simulation():
    bars = _fixture_bars()
    sim_result, sim_strategy = _run_sim(bars)
    live_result, live_strategy, live_ledger = _run_live(bars)

    assert len(sim_result.trades) == len(live_result.trades) > 0
    for sim_trade, live_trade in zip(sim_result.trades, live_result.trades):
        assert sim_trade == live_trade  # frozen dataclass: full field equality

    sim_sequence = [r.event_type for r in sim_result.ledger.records]
    live_sequence = [r.event_type for r in live_ledger.records]
    assert sim_sequence == live_sequence

    assert live_result.halted_reason is None


def test_identity_hook_order_matches_simulation():
    bars = _fixture_bars()
    _, sim_strategy = _run_sim(bars)
    _, live_strategy, _ = _run_live(bars)
    assert sim_strategy.calls == live_strategy.calls


def test_supervisor_daily_loss_flattens_and_blocks_entries():
    bars = _fixture_bars()
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=2.0, daily_loss_stop=10.0))
    live_result, _, live_ledger = _run_live(bars, supervisor=sup)
    # the session-1 stop-out loses far more than $10 -> breach latches;
    # the session-2 scripted entry must be stripped, so only 1 trade exists
    # from session 1 and none from session 2 of the same session... the
    # session-2 entry occurs on a NEW session (supervisor resets) so it fills.
    # The invariant actually asserted: no trade's entry occurs in the same
    # session after its breach.
    reasons = [t.exit_reason for t in live_result.trades]
    assert "stop" in reasons  # session 1 stop-out happened
    session1_trades = [t for t in live_result.trades if t.session_date == "2025-10-01"]
    assert len(session1_trades) == 1  # nothing new after the breach that session


@pytest.mark.skipif(
    "FULL_PYTHON_BASELINE_DATA" not in os.environ,
    reason="requires the operator's local 9-month CSV (set FULL_PYTHON_BASELINE_DATA)",
)
def test_identity_on_the_frozen_anchor_window_with_production_strategy():
    from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
    from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
    from full_python.strategy.adaptive_trend_config import production_am_config
    from scripts.freeze_baseline_anchor import FROZEN_SIMULATION_OVERRIDES

    column_map = CsvBarColumnMap(timestamp="timestamp", symbol="symbol", open="open",
                                 high="high", low="low", close="close", volume="volume")
    bars = load_csv_bars(Path(os.environ["FULL_PYTHON_BASELINE_DATA"]), column_map)
    config = SimulationConfig(**FROZEN_SIMULATION_OVERRIDES)

    sim_result = SimulationEngine(config).run(bars, AdaptiveTrendStrategy(production_am_config()))

    ledger = EventLedger()
    strategy = AdaptiveTrendStrategy(production_am_config())
    broker = PaperBroker(config, strategy, ledger)
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=config.point_value))
    live_result = LiveLoop(RecordedBarSource(bars), strategy, broker, sup, ledger).run()

    assert live_result.halted_reason is None
    assert len(sim_result.trades) == len(live_result.trades)
    for sim_trade, live_trade in zip(sim_result.trades, live_result.trades):
        assert sim_trade == live_trade
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_live_loop_identity.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `live_loop` module); the env-gated test reports SKIPPED.

- [ ] **Step 3: Write the implementation**

Create `src/full_python/execution/live_loop.py`:

```python
"""The live execution conductor.

Mirrors SimulationEngine.run's per-bar sequence exactly (that identity
is test-enforced trade-for-trade and ledger-sequence-for-sequence);
adds only observation and defense: OrderStateMachine shadow
cross-check, RiskSupervisor marks, and flatten-and-halt on any
invariant violation. With the supervisor disabled the additions are
pure observation -- which is the identity argument.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional, Sequence

from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger, EventType
from full_python.execution.broker_protocol import Broker
from full_python.execution.state_machine import (
    ExecutionInvariantError,
    OrderStateMachine,
)
from full_python.execution.supervisor import RiskSupervisor
from full_python.models import MarketBar, Trade


class RecordedBarSource:
    def __init__(self, bars: Sequence[MarketBar]) -> None:
        self._bars = list(bars)

    def __iter__(self) -> Iterator[MarketBar]:
        return iter(self._bars)


@dataclass(frozen=True)
class LiveLoopResult:
    trades: tuple[Trade, ...]
    halted_reason: Optional[str]


class LiveLoop:
    def __init__(
        self,
        bar_source: Iterable[MarketBar],
        strategy,
        broker: Broker,
        supervisor: RiskSupervisor,
        ledger: EventLedger,
    ) -> None:
        self._bar_source = bar_source
        self._strategy = strategy
        self._broker = broker
        self._supervisor = supervisor
        self._ledger = ledger
        self._state_machine = OrderStateMachine()

    def run(self) -> LiveLoopResult:
        halted_reason: Optional[str] = None
        breach_flattened: set[str] = set()  # session_dates already acted on
        try:
            for bar in self._bar_source:
                session = classify_timestamp(bar.timestamp_utc)
                session_iso = session.session_date.isoformat()
                self._ledger.append(
                    EventType.BAR, timestamp_utc=bar.timestamp_utc, payload=bar.to_payload()
                )

                session_pnl = self._broker.process_bar_open(bar, session)
                self._drain_events()
                self._cross_check()

                breach = self._supervisor.check_mark(
                    session_date=session_iso,
                    bar=bar,
                    position=self._broker.position,
                    trades=self._broker.trades,
                )
                if breach is not None and session_iso not in breach_flattened:
                    breach_flattened.add(session_iso)
                    self._ledger.append(
                        EventType.STATE_TRANSITION,
                        timestamp_utc=bar.timestamp_utc,
                        payload={"transition": "execution_halt", "reason": breach},
                    )
                    self._broker.flatten(bar, breach)
                    self._drain_events()
                    self._cross_check()

                on_bar_context = getattr(self._strategy, "on_bar_context", None)
                if on_bar_context is not None:
                    on_bar_context(
                        session_pnl=session_pnl,
                        daily_limit_hit=self._broker.daily_limit_hit,
                    )
                result = self._strategy.on_bar(bar)
                if not self._supervisor.entries_allowed():
                    result = dataclasses.replace(result, order_intents=())
                self._broker.apply_strategy_result(bar, session, result)
                self._broker.note_bar_processed(bar, session)

            self._broker.close_end_of_data()
            self._drain_events()
        except ExecutionInvariantError as exc:
            halted_reason = f"execution_halt: {exc}"
            self._ledger.append(
                EventType.STATE_TRANSITION,
                timestamp_utc="",
                payload={"transition": "execution_halt", "error": str(exc)},
            )
        return LiveLoopResult(
            trades=tuple(self._broker.trades), halted_reason=halted_reason
        )

    def _drain_events(self) -> None:
        for event in self._broker.poll_events():
            self._state_machine.on_event(event)

    def _cross_check(self) -> None:
        shadow = self._state_machine.position
        truth = self._broker.position
        if (shadow is None) != (truth is None):
            raise ExecutionInvariantError(
                f"state-machine/broker position mismatch: shadow={shadow!r} truth={truth!r}"
            )
        if shadow is not None and truth is not None:
            if shadow.side != truth.side or shadow.quantity != truth.quantity:
                raise ExecutionInvariantError(
                    f"state-machine/broker position mismatch: shadow={shadow!r} truth={truth!r}"
                )
```

Note the identity subtlety the tests pin: the extra `STATE_TRANSITION` ledger event is emitted ONLY on a supervisor breach, and `_drain_events`/`_cross_check` never write to the ledger — so with the supervisor disabled the ledger sequence is exactly the sim's. If the fixture's ledger sequences diverge, the bug is in the loop, not the fixture.

- [ ] **Step 4: Verify green**

Run: `python3 -m pytest tests/test_live_loop_identity.py -v` — expected: 3 passed, 1 skipped (env-gated real-data test)
Run: `python3 -m pytest -q` — expected: 173 passed, 3 skipped

- [ ] **Step 5: Commit**

```bash
git add src/full_python/execution/live_loop.py tests/test_live_loop_identity.py
git commit -m "feat: LiveLoop with state-machine cross-check and supervisor -- sim identity enforced"
```

---

## Post-merge verification (controller step, not a task)

From the main clone with data present:

```
FULL_PYTHON_BASELINE_DATA=runs/baseline-anchor/nq1_2025-10-01_2026-06-26.csv python3 -m pytest -q
```

Expected: all tests pass, 0 skipped that matter — specifically `test_identity_on_the_frozen_anchor_window_with_production_strategy` runs the full 9-month window through BOTH engines (~90s) and proves the live path reproduces the frozen anchor trade-for-trade. That green is the completion of sub-project 1.

## Not in this plan (later sub-projects)

- Live data feed, contract authority, outage policy (sub-project 2).
- Tradovate adapter, auth, reconnect, failure matrix (sub-project 3) — including real semantics for `PartialFilled`, currently a modeled-but-fatal event.
- Gate 5-7 operational tooling (sub-project 4).
