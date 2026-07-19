"""Adversarial failure matrix, end-to-end through the REAL composition.

The 2026-07-13 principal audit verified the broker's components but found
"nothing calls them" (P1-6). Every scenario here drives `build_order_session`
itself: schema-strict fake REST that auto-queues protocol-faithful user-sync
props events, a scripted user-sync websocket, a bar source that invokes the
maintenance hook between bars exactly like `bars_until`, and the real
journal. Audit-matrix rows proven at composition level are noted per test.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from full_python.events import EventLedger
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.models import ExitDecision, MarketBar, OrderIntent, StrategyResult
from full_python.tradovate.account_sync import AccountHydrationSnapshot
from full_python.tradovate.broker import BrokerPosition, TradovateBroker  # noqa: F401
from full_python.tradovate.config import DEMO_ENVIRONMENT, TradovateAdapterConfig
from full_python.execution.order_intent_journal import IntentState, OrderIntentJournal
from full_python.live.order_runner import build_order_session, run_startup_flatten


def _cfg(**overrides):
    values = dict(
        environment=DEMO_ENVIRONMENT,
        account_spec="DEMO123",
        account_id=456,
        root_symbol="NQ",
        contract_symbol="NQU6",
        contract_id=789,
        order_enabled=True,
        flatten_enabled=True,
        dollar_point_value=20.0,
        commission_per_contract_round_trip=1.0,
        daily_loss_limit=1000.0,
    )
    values.update(overrides)
    return TradovateAdapterConfig(**values)


def _flat_snapshot(trade_date):
    return AccountHydrationSnapshot(
        account_id=456,
        account_spec="DEMO123",
        contract_id=789,
        contract_symbol="NQU6",
        position=None,
        working_orders=(),
        orders_by_id={},
        commands_by_client_id={},
        trade_date=trade_date,
        daily_realized_pnl=0.0,
        entry_permitted=True,
    )


def _terminal_snapshot(journal, canceled_bodies, trade_date):
    orders_by_id = {}
    commands = {}
    for record in journal.latest_by_intent.values():
        if record.state != IntentState.ACKNOWLEDGED:
            continue
        orders_by_id[record.broker_order_id] = {
            "id": int(record.broker_order_id), "ordStatus": "Filled",
        }
        commands[record.client_operation_id] = {
            "id": int(record.broker_order_id) + 1000,
            "orderId": int(record.broker_order_id),
            "isAutomated": True,
        }
    for body in canceled_bodies:
        order_id = int(body["orderId"])
        orders_by_id[str(order_id)] = {"id": order_id, "ordStatus": "Canceled"}
        commands[body["clOrdId"]] = {
            "id": order_id + 2000, "orderId": order_id, "isAutomated": True,
        }
    return replace(
        _flat_snapshot(trade_date),
        orders_by_id=orders_by_id,
        commands_by_client_id=commands,
    )


class ServerSim:
    """Schema-strict fake Tradovate for both the REST and user-sync seams."""

    def __init__(self):
        self.ws_queue = []
        self.placed = []
        self.canceled = []
        self.liquidations = []
        self.rest_positions = []
        self.heartbeats = 0
        self.mark_price = 20100.25
        self._next_id = 100
        self._net_pos = 0

    # -- user-sync websocket seam ----------------------------------------
    def send_heartbeat(self):
        self.heartbeats += 1

    def receive_event(self, wait_seconds):
        # Faithful to the real client: nonpositive wait never reads the
        # transport (review 2026-07-19, P0-1).
        if wait_seconds <= 0:
            return None
        if self.ws_queue:
            return self.ws_queue.pop(0)
        return None

    # -- protocol-faithful event builders --------------------------------
    def _queue_fill(self, order_id, action, qty, price):
        self._net_pos += qty if action == "Buy" else -qty
        self.ws_queue.append({
            "e": "props",
            "d": {"entityType": "fill", "eventType": "Created", "entity": {
                "id": order_id + 5000,
                "orderId": order_id,
                "contractId": 789,
                "timestamp": "2026-07-07T14:32:30Z",
                "action": action,
                "qty": qty,
                "price": price,
            }},
        })

    def queue_order_canceled(self, order_id):
        self.ws_queue.append({
            "e": "props",
            "d": {"entityType": "order", "eventType": "Updated", "entity": {
                "id": order_id, "accountId": 456, "ordStatus": "Canceled",
            }},
        })

    # -- REST order seam (schema-strict) ---------------------------------
    def order_place(self, body):
        required = {"accountSpec", "accountId", "action", "symbol",
                    "orderQty", "orderType", "isAutomated"}
        assert required.issubset(body), f"order body missing keys: {body}"
        assert body["accountId"] == 456 and body["accountSpec"] == "DEMO123"
        assert body["symbol"] == "NQU6" and body["orderQty"] == 1
        self.placed.append(dict(body))
        self._next_id += 1
        if body["orderType"] == "Market":
            self._queue_fill(self._next_id, body["action"], 1, self.mark_price)
        return {"orderId": self._next_id}

    def order_cancel(self, body):
        assert {"orderId", "isAutomated", "clOrdId"}.issubset(body), body
        self.canceled.append(dict(body))
        self.queue_order_canceled(int(body["orderId"]))
        return {}

    def order_liquidate_position(self, body):
        assert set(body) == {
            "accountId", "contractId", "admin", "isAutomated", "customTag50",
        }, body
        assert body["accountId"] == 456 and body["contractId"] == 789
        self.liquidations.append(dict(body))
        self._next_id += 1
        action = "Sell" if self._net_pos > 0 else "Buy"
        self._queue_fill(self._next_id, action, abs(self._net_pos) or 1,
                         self.mark_price)
        return {"orderId": self._next_id}

    # -- REST reconciliation seam ----------------------------------------
    def position_list(self):
        return list(self.rest_positions)


class MaintenanceBarSource:
    """Invokes the maintenance hook before each bar, like bars_until."""

    def __init__(self, bars):
        self._bars = list(bars)
        self.maintenance = None

    def factory(self, maintenance):
        self.maintenance = maintenance
        return self

    def __iter__(self):
        for bar in self._bars:
            self.maintenance()
            yield bar


class ScriptedStrategy:
    """Emits scripted intents/exits per bar index; records feedback."""

    def __init__(self, entries=(), exits=(), stop_offset=30.0):
        self._entries = set(entries)
        self._exits = set(exits)
        self._stop_offset = stop_offset
        self._index = -1
        self.fills = []
        self.closed = []

    def on_fill(self, fill):
        self.fills.append(fill)

    def on_trade_closed(self, trade):
        self.closed.append(trade)

    def on_bar_context(self, *, session_pnl, daily_limit_hit):
        return None

    def on_bar(self, bar):
        self._index += 1
        if self._index in self._entries:
            return StrategyResult(order_intents=(
                OrderIntent.market_entry(
                    timestamp_utc=bar.timestamp_utc, symbol="NQU6", side="buy",
                    quantity=1, reason="matrix",
                    metadata={"stop_price": bar.close - self._stop_offset},
                ),
            ))
        if self._index in self._exits:
            return StrategyResult(exits=(
                ExitDecision(timestamp_utc=bar.timestamp_utc, symbol="NQU6",
                             reason="atf_flip"),
            ))
        return StrategyResult()


def _bars(timestamps, close=20100.25, **overrides):
    bars = []
    for ts in timestamps:
        values = dict(timestamp_utc=ts, symbol="NQU6", open=close, high=close + 1,
                      low=close - 1, close=close, volume=1.0)
        values.update(overrides)
        bars.append(MarketBar(**values))
    return bars


def _session_pieces(tmp_path, server, strategy, bars, *, config=None, clock=None):
    source = MaintenanceBarSource(bars)
    journal = OrderIntentJournal(tmp_path / "orders.jsonl", run_id="matrix")
    kwargs = dict(
        config=config or _cfg(),
        rest_client=server,
        user_sync_ws=server,
        strategy=strategy,
        supervisor=RiskSupervisor(RiskSupervisorConfig(point_value=20.0)),
        ledger=EventLedger(),
        bar_source_factory=source.factory,
        intent_journal=journal,
    )
    if clock is not None:
        kwargs["monotonic_clock"] = clock
        kwargs["reconciliation_interval_seconds"] = 30.0
    session = build_order_session(**kwargs)
    return session, journal


def _halt_payloads(session):
    return [
        record.payload for record in session.loop._ledger.records
        if record.payload.get("transition") == "execution_halt"
    ]


# Rows 5/6-class: full round trip with exactly-once broker-authoritative
# feedback through the composed stack.
def test_e2e_entry_stop_exit_round_trip(tmp_path):
    server = ServerSim()
    strategy = ScriptedStrategy(entries={0}, exits={2})
    bars = _bars([
        "2026-07-07T14:32:00Z", "2026-07-07T14:33:00Z",
        "2026-07-07T14:34:00Z", "2026-07-07T14:35:00Z",
        "2026-07-07T14:36:00Z",
    ])
    session, _ = _session_pieces(tmp_path, server, strategy, bars)
    session.broker.hydrate_account_state(_flat_snapshot("2026-07-07"))

    result = session.loop.run()

    assert result.halted_reason is None
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "atf_flip"
    assert session.broker.position is None
    assert len(strategy.fills) == 1 and len(strategy.closed) == 1
    # entry + protective stop + market exit all hit the schema-strict server
    assert len(server.placed) == 3
    assert len(server.canceled) == 1


# Rows 4+18: DLL breach runs the STAGED flatten through the pump and ends
# NORMAL with entries vetoed daily_limit (not a dead recovery latch).
def test_e2e_dll_breach_staged_flatten_then_veto(tmp_path):
    server = ServerSim()
    strategy = ScriptedStrategy(entries={0, 3})
    bars = _bars(["2026-07-07T14:32:00Z"]) + _bars(
        ["2026-07-07T14:33:00Z", "2026-07-07T14:34:00Z",
         "2026-07-07T14:35:00Z"], close=20040.0,
    )
    session, _ = _session_pieces(tmp_path, server, strategy, bars)
    session.broker.hydrate_account_state(_flat_snapshot("2026-07-07"))
    # entry fills at 20100.25; bar close 20040 is -60.25pt * $20 = -$1,205

    result = session.loop.run()

    assert result.halted_reason is None
    assert session.broker.daily_limit_hit is True
    assert session.broker.position is None
    assert len(server.liquidations) == 1
    assert len(result.trades) == 1 and result.trades[0].exit_reason == "daily_limit"
    # the bar-3 entry was vetoed with the sim-identical reason, no 4th order
    assert len(server.placed) == 2  # entry + protective stop only


# Row 16: a full-holiday session vetoes market_closed before any REST call.
def test_e2e_market_closed_vetoes_before_any_post(tmp_path):
    server = ServerSim()
    strategy = ScriptedStrategy(entries={0})
    bars = _bars(["2025-12-25T15:00:00Z", "2025-12-25T15:01:00Z"])
    session, _ = _session_pieces(tmp_path, server, strategy, bars)
    session.broker.hydrate_account_state(_flat_snapshot("2025-12-25"))

    result = session.loop.run()

    assert result.halted_reason is None
    assert server.placed == [] and server.liquidations == []
    assert result.trades == ()


# Row 17: a REAL early-close date (2025-11-28, 13:15 close) triggers the
# broker-side backstop at close-1 through the composed stack.
def test_e2e_early_close_backstop_flattens(tmp_path):
    server = ServerSim()
    strategy = ScriptedStrategy(entries={0})
    bars = _bars([
        "2025-11-28T17:50:00Z",  # 12:50 ET
        "2025-11-28T17:51:00Z",
        "2025-11-28T18:14:00Z",  # 13:14 ET = close-1 -> backstop
        "2025-11-28T18:15:00Z",  # resolution pumped before this bar
    ])
    session, _ = _session_pieces(tmp_path, server, strategy, bars)
    session.broker.hydrate_account_state(_flat_snapshot("2025-11-28"))

    result = session.loop.run()

    assert result.halted_reason is None
    assert session.broker.position is None
    assert len(server.liquidations) == 1
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "session_close_backstop"


# Row 12: a fill for an unknown order id halts WITHOUT flatten, through the
# maintenance wrapper into LiveLoop's ledgered invariant-halt path.
def test_e2e_unknown_order_fill_is_a_ledgered_invariant_halt(tmp_path):
    server = ServerSim()
    server._queue_fill(999, "Buy", 1, 20100.25)
    strategy = ScriptedStrategy()
    bars = _bars(["2026-07-07T14:32:00Z"])
    session, _ = _session_pieces(tmp_path, server, strategy, bars)
    session.broker.hydrate_account_state(_flat_snapshot("2026-07-07"))

    result = session.loop.run()

    assert result.halted_reason is not None
    assert result.halted_reason.startswith("execution_halt")
    assert server.liquidations == []  # invariant halt does NOT flatten
    halts = _halt_payloads(session)
    assert halts and halts[0]["reason"] == "invariant_violation"


# Row 14: REST/fill-derived position disagreement via the pump's
# reconciliation interval halts the composed stack.
def test_e2e_rest_position_drift_halts(tmp_path):
    class SteppingClock:
        def __init__(self):
            self.value = 0.0

        def __call__(self):
            self.value += 31.0
            return self.value

    server = ServerSim()
    server.rest_positions = [{
        "accountId": 456, "contractId": 789, "netPos": 1,
    }]
    strategy = ScriptedStrategy()
    bars = _bars(["2026-07-07T14:32:00Z"])
    session, _ = _session_pieces(
        tmp_path, server, strategy, bars, clock=SteppingClock()
    )
    session.broker.hydrate_account_state(_flat_snapshot("2026-07-07"))

    result = session.loop.run()

    assert result.halted_reason is not None
    assert result.halted_reason.startswith("execution_halt")
    assert _halt_payloads(session)


# Row 15: restart with an open position -- the startup flatten runs through
# run_startup_flatten + the pump, then a fresh journal-correlated hydration
# reopens, then a clean session runs.
def test_e2e_startup_flatten_then_clean_session(tmp_path):
    server = ServerSim()
    server._net_pos = 1  # the server knows about the inherited long
    strategy = ScriptedStrategy()
    bars = _bars(["2026-07-07T14:32:00Z", "2026-07-07T14:33:00Z"])
    session, journal = _session_pieces(tmp_path, server, strategy, bars)

    inherited = replace(
        _flat_snapshot("2026-07-07"),
        position=BrokerPosition(side="long", quantity=1, entry_price=20100.25),
        working_orders=({
            "id": 555, "ordStatus": "Working", "contractId": 789,
            "action": "Sell", "orderQty": 1,
        },),
        entry_permitted=False,
    )
    session.broker.startup_flatten(inherited, timestamp_utc="2026-07-07T14:30:00Z")
    run_startup_flatten(session.broker, session.pump, timeout_seconds=5.0,
                        wait_seconds=0.25)

    assert session.broker.position is None
    assert session.broker.flatten_in_progress is False
    assert len(server.liquidations) == 1
    assert session.broker.poll_strategy_feedback() == []

    session.broker.hydrate_account_state(
        _terminal_snapshot(journal, server.canceled, "2026-07-07")
    )
    result = session.loop.run()
    assert result.halted_reason is None
    assert result.trades == ()
