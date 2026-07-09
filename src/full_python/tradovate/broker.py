"""Tradovate broker safety skeleton.

This adapter deliberately contains no live-order enablement by default. It
only calls the injected REST client when explicit config gates are enabled.

**NOT SAFE TO ENABLE LIVE ORDER ROUTING YET.** `order_enabled=True` is
gated correctly (no order is placed unless the flag is set, and a missing
`stop_price` is rejected before submission), but the risk-management and
order-lifecycle layers behind that gate are NOT feature-complete. Tracked
gaps, found and recorded 2026-07-10 (see the dated amendment in
docs/superpowers/specs/2026-07-07-tradovate-adapter-design.md), all of
which must be closed before `order_enabled=True` is ever used against a
funded account:

1. `daily_limit_hit` is a stub that never updates -- the strategy's own
   validated $1,000 DLL veto (AdaptiveTrendStrategy) can never fire live.
2. `process_bar_open` always returns 0.0 -- the projected-risk position-
   sizing guard never shrinks with intraday losses (frozen-open budget).
3. `trades` always returns [] -- RiskSupervisor's daily-loss backstop only
   sees the open position's unrealized P&L, never realized session losses.
4. No protective stop/OCO is ever submitted after an entry fill, despite
   `stop_price` being validated -- an enabled entry is a NAKED position.
5. `apply_strategy_result` only processes `result.order_intents`; it drops
   `result.exits` and `result.stop_updates` entirely -- there is no path
   for the strategy to close a position through this broker.
6. `ingest_raw_event` has no submitted-order-id map -- a fill for an
   unrecognized order id is silently treated as a real position update,
   which also defeats LiveLoop._cross_check()'s divergence detection.

None of this is reachable today (`order_enabled`/`flatten_enabled` default
False and no live adapter is wired into LiveLoop), but it was previously
undocumented, which is the actual risk: a future change enabling order
routing without independently rediscovering this list.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from full_python.data.sessions import SessionInfo
from full_python.execution.broker_protocol import (
    Acked,
    BrokerEvent,
    BrokerPosition,
    Filled,
    PartialFilled,
    Rejected,
)
from full_python.models import MarketBar, StrategyResult, Trade
from full_python.tradovate.config import TradovateAdapterConfig
from full_python.tradovate.errors import TradovateOrderSafetyError


@dataclass(frozen=True)
class TradovateRawEvent:
    kind: str
    data: dict[str, Any]


class TradovateBroker:
    def __init__(self, config: TradovateAdapterConfig, rest_client: Any) -> None:
        self._config = config
        self._rest_client = rest_client
        self._events: list[BrokerEvent] = []
        self._position: Optional[BrokerPosition] = None
        self._trades: list[Trade] = []
        # STUB (tracked gap #1, see module docstring): never mutated by this
        # class. PaperBroker's equivalent property reflects a real equity-
        # based DLL computed by the shared PositionEngine; this always
        # returns False, so AdaptiveTrendStrategy's $1,000 DLL veto is dead
        # code on this broker. Must be wired to real session P&L before
        # order_enabled=True is used live.
        self._daily_limit_hit = False

    def process_bar_open(self, bar: MarketBar, session: SessionInfo) -> float:
        # STUB (tracked gap #2, see module docstring): always 0.0. LiveLoop
        # feeds this to strategy.on_bar_context as session_pnl, which the
        # projected-risk position-sizing guard (f_projected_dll_safe_qty)
        # uses to shrink size as the session loses money. Frozen at 0.0, the
        # guard never sees a loss and never shrinks -- sizing behaves as if
        # every bar is the first bar of a breakeven session.
        return 0.0

    def apply_strategy_result(
        self, bar: MarketBar, session: SessionInfo, result: StrategyResult
    ) -> None:
        # TRACKED GAP #5 (see module docstring): only result.order_intents is
        # processed. result.exits and result.stop_updates are silently
        # dropped -- there is currently no path for the strategy to close a
        # position through this broker (only RiskSupervisor.flatten() can).
        for intent in result.order_intents:
            if not self._config.order_enabled:
                self._events.append(Rejected(order_id="", reason="order_disabled"))
                continue
            if "stop_price" not in intent.metadata:
                raise TradovateOrderSafetyError("stop_price metadata required")

            # TRACKED GAP #4 (see module docstring): stop_price is validated
            # above but never submitted as a protective order. Per the design
            # spec (docs/superpowers/specs/2026-07-07-tradovate-adapter-
            # design.md, tradovate/broker.py responsibilities), a filled
            # entry must be followed by a broker-held stop (OCO with target
            # when one exists), and a confirmation failure must force a
            # flatten + fatal state error. None of that is implemented here
            # -- an order_enabled=True entry currently fills NAKED.
            body = {
                "accountSpec": self._config.account_spec,
                "accountId": self._config.account_id,
                "action": _action_from_side(intent.side),
                "symbol": intent.symbol,
                "orderQty": intent.quantity,
                "orderType": "Market",
                "isAutomated": True,
            }
            response = self._rest_client.order_place(body)
            self._events.append(Acked(order_id=str(response["orderId"])))

    def note_bar_processed(self, bar: MarketBar, session: SessionInfo) -> None:
        return None

    def close_end_of_data(self) -> None:
        return None

    def flatten(self, bar: MarketBar, reason: str) -> None:
        if not self._config.flatten_enabled:
            raise TradovateOrderSafetyError("flatten_disabled")
        if self._position is None:
            return

        self._rest_client.order_liquidate_position({
            "accountSpec": self._config.account_spec,
            "accountId": self._config.account_id,
            "symbol": bar.symbol,
            "admin": False,
        })

    def poll_events(self) -> list[BrokerEvent]:
        events = list(self._events)
        self._events.clear()
        return events

    def ingest_raw_event(self, event: TradovateRawEvent) -> None:
        # TRACKED GAP #6 (see module docstring): there is no submitted-
        # order-id map here (the design spec calls for one: "submitted order
        # map: client/local id to Tradovate order id"). A "fill" event for
        # an order id this broker never submitted -- e.g. a stale/duplicate
        # message, or a fill from a different session -- is indistinguishable
        # from a real one below and is applied as a genuine position update.
        # This also silently defeats LiveLoop._cross_check(), since
        # self._position is updated in lockstep with the phantom fill.
        if event.kind == "position":
            self._position = _position_from_data(event.data)
            return
        if event.kind == "partial_fill":
            self._events.append(_partial_fill_from_data(event.data))
            return
        if event.kind == "fill":
            fill = _fill_from_data(event.data)
            self._position = BrokerPosition(
                side="long" if fill.side == "buy" else "short",
                quantity=fill.quantity,
                entry_price=fill.price,
            )
            self._events.append(fill)
            return
        raise TradovateOrderSafetyError("unknown_tradovate_event_kind")

    @property
    def position(self) -> Optional[BrokerPosition]:
        return self._position

    @property
    def trades(self) -> list[Trade]:
        # STUB (tracked gap #3, see module docstring): self._trades is never
        # appended to -- there is no closed-trade reconstruction from broker
        # fills yet. RiskSupervisor.check_mark sums this list as "realized"
        # session P&L (execution/supervisor.py); with this always empty, the
        # independent daily-loss backstop only ever sees the open position's
        # unrealized P&L and never accumulates realized losses within a
        # session -- a day of losing round-trips would not trip it.
        return list(self._trades)

    @property
    def daily_limit_hit(self) -> bool:
        return self._daily_limit_hit


def _action_from_side(side: str) -> str:
    normalized = side.lower()
    if normalized == "buy":
        return "Buy"
    if normalized == "sell":
        return "Sell"
    raise TradovateOrderSafetyError("unsupported_order_side")


def _side_from_action(action: Any) -> str:
    if action == "Buy":
        return "buy"
    if action == "Sell":
        return "sell"
    raise TradovateOrderSafetyError("unsupported_order_action")


def _position_from_data(data: dict[str, Any]) -> BrokerPosition:
    side = data.get("side")
    if side not in {"long", "short"}:
        raise TradovateOrderSafetyError("unsupported_position_side")
    price = data.get("price", data.get("entryPrice"))
    if price is None:
        raise TradovateOrderSafetyError("position_price_required")
    return BrokerPosition(
        side=side,
        quantity=int(data["qty"]),
        entry_price=float(price),
    )


def _fill_from_data(data: dict[str, Any]) -> Filled:
    return Filled(
        order_id=str(data["orderId"]),
        side=_side_from_action(data["action"]),
        quantity=int(data["qty"]),
        price=float(data["price"]),
        timestamp_utc=str(data["timestamp"]),
        reason=str(data.get("reason", "")),
    )


def _partial_fill_from_data(data: dict[str, Any]) -> PartialFilled:
    return PartialFilled(
        order_id=str(data["orderId"]),
        side=_side_from_action(data["action"]),
        quantity=int(data["qty"]),
        remaining=int(data["remaining"]),
        price=float(data["price"]),
        timestamp_utc=str(data["timestamp"]),
    )
