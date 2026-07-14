"""LiveLoop-level integration for the Tradovate adapter (offline).

A scripted bar source ingests raw broker events between bars, so fills
flow through the REAL LiveLoop sequence: process_bar_open -> drain ->
cross-check -> supervisor -> strategy -> apply_strategy_result.
"""
from __future__ import annotations

from typing import Iterator, Optional

from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger, EventType
from full_python.execution.live_loop import LiveLoop
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.models import MarketBar, OrderIntent, StrategyResult
from full_python.tradovate.broker import TradovateBroker, TradovateRawEvent
from full_python.tradovate.config import DEMO_ENVIRONMENT, TradovateAdapterConfig


class FakeRestClient:
    """Local copy -- tests/ is not a package, so no cross-test imports."""

    def __init__(self):
        self.placed = []
        self.canceled = []
        self.liquidations = []
        self._auto_id = 100

    def order_place(self, body):
        self.placed.append(body)
        self._auto_id += 1
        return {"orderId": self._auto_id}

    def order_cancel(self, body):
        self.canceled.append(body)
        return {}

    def order_liquidate_position(self, body):
        assert set(body) == {"accountId", "contractId", "admin"}
        self.liquidations.append(body)
        self._auto_id += 1
        return {"orderId": self._auto_id}


def _bar(ts: str, price: float) -> MarketBar:
    return MarketBar(timestamp_utc=ts, symbol="NQU6", open=price, high=price,
                     low=price, close=price, volume=1.0)


def _fill(order_id: int, action: str, price: float, ts: str) -> TradovateRawEvent:
    return TradovateRawEvent(kind="fill", data={
        "orderId": order_id, "action": action, "qty": 1,
        "price": price, "timestamp": ts, "reason": "",
        "accountId": 456, "contractId": 789,
    })


class ScriptedBarSource:
    """Yields bars; before each bar, ingests that bar's scripted raw events."""

    def __init__(self, broker: TradovateBroker, bars, events_by_index) -> None:
        self._broker = broker
        self._bars = list(bars)
        self._events_by_index = dict(events_by_index)

    def __iter__(self) -> Iterator[MarketBar]:
        for i, bar in enumerate(self._bars):
            for event in self._events_by_index.get(i, []):
                self._broker.ingest_raw_event(event)
            yield bar


class ScriptedStrategy:
    """Emits an entry intent on scripted bar indices; records DLL context."""

    def __init__(self, entry_indices) -> None:
        self._entry_indices = set(entry_indices)
        self._index = -1
        self.contexts = []

    def on_bar_context(self, *, session_pnl: float, daily_limit_hit: bool) -> None:
        self.contexts.append((session_pnl, daily_limit_hit))

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        self._index += 1
        if self._index in self._entry_indices:
            return StrategyResult(order_intents=(
                OrderIntent.market_entry(
                    timestamp_utc=bar.timestamp_utc, symbol="NQU6", side="buy",
                    quantity=1, reason="scripted", metadata={"stop_price": bar.close - 30.0},
                ),
            ))
        return StrategyResult()


class FillAwareStrategy:
    """Keeps requesting entry until broker-authoritative feedback arrives."""

    def __init__(self) -> None:
        self.position_side = None
        self.fill_calls = []

    def on_fill(self, fill) -> None:
        self.fill_calls.append(fill)
        self.position_side = "long" if fill.side == "buy" else "short"

    def on_trade_closed(self, trade) -> None:
        self.position_side = None

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        if self.position_side is not None:
            return StrategyResult()
        return StrategyResult(order_intents=(
            OrderIntent.market_entry(
                timestamp_utc=bar.timestamp_utc,
                symbol="NQU6",
                side="buy",
                quantity=1,
                reason="sr_breakout",
                metadata={"stop_price": bar.close - 30.0},
            ),
        ))


def _cfg() -> TradovateAdapterConfig:
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT, account_spec="DEMO123", account_id=456,
        root_symbol="NQ", contract_symbol="NQU6", contract_id=789,
        order_enabled=True, flatten_enabled=True,
        dollar_point_value=20.0, commission_per_contract_round_trip=1.0,
        daily_loss_limit=1000.0,
    )


def test_losing_round_trips_trip_dll_and_supervisor_through_live_loop() -> None:
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(), rest)
    strategy = ScriptedStrategy(entry_indices=[0, 2])
    ts = ["2026-07-07T14:3%d:00Z" % i for i in range(1, 7)]
    bars = [_bar(ts[0], 100.0), _bar(ts[1], 100.0), _bar(ts[2], 100.0),
            _bar(ts[3], 100.0), _bar(ts[4], 100.0), _bar(ts[5], 100.0)]
    events = {
        1: [_fill(101, "Buy", 100.0, ts[0])],          # entry 1 fills (stop = 102)
        2: [_fill(102, "Sell", 70.0, ts[1])],          # stop fills: -601 net
        3: [_fill(103, "Buy", 100.0, ts[2])],          # entry 2 fills (stop = 104)
        4: [_fill(104, "Sell", 70.0, ts[3])],          # stop fills: -1202 net total
    }
    supervisor = RiskSupervisor(RiskSupervisorConfig(point_value=20.0, daily_loss_stop=1100.0))
    ledger = EventLedger()
    loop = LiveLoop(ScriptedBarSource(broker, bars, events), strategy, broker, supervisor, ledger)

    result = loop.run()

    assert result.halted_reason is None
    assert len(result.trades) == 2
    assert sum(t.net_pnl for t in result.trades) == -1202.0
    # strategy-facing DLL flag flipped once realized losses breached $1,000
    assert any(hit for (_pnl, hit) in strategy.contexts)
    # supervisor breach recorded in the ledger with its reason
    halts = [r for r in ledger.records if r.event_type == EventType.STATE_TRANSITION
             and r.payload.get("transition") == "execution_halt"]
    assert any(r.payload["reason"] == "supervisor_daily_loss" for r in halts)


def test_unknown_fill_halts_live_loop_without_flatten() -> None:
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(), rest)
    strategy = ScriptedStrategy(entry_indices=[])
    bars = [_bar("2026-07-07T14:31:00Z", 100.0), _bar("2026-07-07T14:32:00Z", 100.0)]
    events = {1: [_fill(999, "Buy", 100.0, "2026-07-07T14:31:30Z")]}  # platform/manual fill
    supervisor = RiskSupervisor(RiskSupervisorConfig(point_value=20.0))
    ledger = EventLedger()
    loop = LiveLoop(ScriptedBarSource(broker, bars, events), strategy, broker, supervisor, ledger)

    result = loop.run()

    assert result.halted_reason is not None
    assert "unknown order id 999" in result.halted_reason
    halts = [r for r in ledger.records if r.event_type == EventType.STATE_TRANSITION]
    assert halts[-1].payload["reason"] == "invariant_violation"
    assert rest.liquidations == []   # invariant halt: no flatten, position truth unknown


def test_authoritative_fill_reaches_strategy_before_next_bar_decision() -> None:
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(), rest)
    strategy = FillAwareStrategy()
    bars = [
        _bar("2026-07-07T14:31:00Z", 100.0),
        _bar("2026-07-07T14:32:00Z", 101.0),
    ]
    source = ScriptedBarSource(
        broker,
        bars,
        {1: [_fill(101, "Buy", 100.5, "2026-07-07T14:31:30Z")]},
    )
    loop = LiveLoop(
        source,
        strategy,
        broker,
        RiskSupervisor(RiskSupervisorConfig(point_value=20.0)),
        EventLedger(),
    )

    result = loop.run()

    assert result.halted_reason is None
    entry_orders = [body for body in rest.placed if body["orderType"] == "Market"]
    assert len(entry_orders) == 1
    assert len(strategy.fill_calls) == 1
    assert strategy.fill_calls[0].reason == "sr_breakout"
