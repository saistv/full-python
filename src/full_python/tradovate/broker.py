"""Tradovate broker safety skeleton.

This adapter deliberately contains no live-order enablement by default. It
only calls the injected REST client when explicit config gates are enabled.
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
        self._daily_limit_hit = False

    def process_bar_open(self, bar: MarketBar, session: SessionInfo) -> float:
        return 0.0

    def apply_strategy_result(
        self, bar: MarketBar, session: SessionInfo, result: StrategyResult
    ) -> None:
        for intent in result.order_intents:
            if not self._config.order_enabled:
                self._events.append(Rejected(order_id="", reason="order_disabled"))
                continue
            if "stop_price" not in intent.metadata:
                raise TradovateOrderSafetyError("stop_price metadata required")

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
    return BrokerPosition(
        side=side,
        quantity=int(data["qty"]),
        entry_price=float(data["entryPrice"]),
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
