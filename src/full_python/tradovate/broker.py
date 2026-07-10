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

from full_python.data.sessions import SessionInfo, classify_timestamp
from full_python.execution.broker_protocol import (
    Acked,
    BrokerEvent,
    BrokerPosition,
    Canceled,
    Filled,
    PartialFilled,
    Rejected,
)
from full_python.models import MarketBar, StrategyResult, Trade
from full_python.risk.daily_loss import is_daily_loss_breached
from full_python.tradovate.config import TradovateAdapterConfig
from full_python.tradovate.errors import (
    TradovateConfigError,
    TradovateError,
    TradovateOrderSafetyError,
    TradovateStateError,
)
from full_python.tradovate.ledger import FillPairingLedger


@dataclass(frozen=True)
class TradovateRawEvent:
    kind: str
    data: dict[str, Any]


ROLE_ENTRY = "entry"
ROLE_PROTECTIVE_STOP = "protective_stop"
ROLE_EXIT = "exit"


@dataclass
class SubmittedOrder:
    order_id: str
    role: str  # ROLE_ENTRY | ROLE_PROTECTIVE_STOP | ROLE_EXIT
    side: str  # "buy" | "sell"
    quantity: int
    symbol: str
    stop_price: Optional[float] = None
    reason: str = ""
    status: str = "working"  # "working" | "filled" | "canceled" | "rejected"


class TradovateBroker:
    def __init__(self, config: TradovateAdapterConfig, rest_client: Any) -> None:
        if config.dollar_point_value is None:
            raise TradovateConfigError(
                "TradovateBroker requires dollar_point_value "
                "(per-instrument: NQ=20.0, MNQ=2.0 -- never reuse across instruments)"
            )
        if config.order_enabled:
            if config.daily_loss_limit is None:
                raise TradovateConfigError("order_enabled requires daily_loss_limit")
            if not config.flatten_enabled:
                raise TradovateConfigError(
                    "order_enabled requires flatten_enabled "
                    "(a DLL breach or failed protective stop must be able to flatten)"
                )
        self._config = config
        self._rest_client = rest_client
        self._events: list[BrokerEvent] = []
        self._orders: dict[str, SubmittedOrder] = {}
        self._fill_ledger = FillPairingLedger(
            dollar_point_value=config.dollar_point_value,
            commission_per_contract_round_trip=config.commission_per_contract_round_trip,
        )
        self._position: Optional[BrokerPosition] = None
        self._working_stop_id: Optional[str] = None
        self._previous_session: Optional[SessionInfo] = None
        self._daily_limit_hit = False

    # -- per-bar hooks (LiveLoop sequence) --------------------------------

    def process_bar_open(self, bar: MarketBar, session: SessionInfo) -> float:
        # STUB until Task 6: session P&L / DLL wiring lands there.
        return 0.0

    def apply_strategy_result(
        self, bar: MarketBar, session: SessionInfo, result: StrategyResult
    ) -> None:
        # result.stop_updates deliberately never applied (production policy
        # freezes stops at entry -- PositionEngine logs them applied=False
        # and this adapter matches).
        for exit_decision in result.exits:
            if self._position is None:
                continue  # mirror PositionEngine: exit with no position is a no-op
            if not self._config.order_enabled:
                self._events.append(Rejected(order_id="", reason="order_disabled"))
                continue
            self._cancel_working_stop_or_halt()
            action = "Sell" if self._position.side == "long" else "Buy"
            body = {
                "accountSpec": self._config.account_spec,
                "accountId": self._config.account_id,
                "action": action,
                "symbol": bar.symbol,
                "orderQty": self._position.quantity,
                "orderType": "Market",
                "isAutomated": True,
            }
            response = self._rest_client.order_place(body)
            order_id = str(response["orderId"])
            self._orders[order_id] = SubmittedOrder(
                order_id=order_id,
                role=ROLE_EXIT,
                side=action.lower(),
                quantity=self._position.quantity,
                symbol=bar.symbol,
                reason=exit_decision.reason,
            )
            self._events.append(Acked(order_id=order_id))
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
            order_id = str(response["orderId"])
            self._orders[order_id] = SubmittedOrder(
                order_id=order_id,
                role=ROLE_ENTRY,
                side=intent.side.lower(),
                quantity=intent.quantity,
                symbol=intent.symbol,
                stop_price=float(intent.metadata["stop_price"]),
                reason=intent.reason,
            )
            self._events.append(Acked(order_id=order_id))

    def note_bar_processed(self, bar: MarketBar, session: SessionInfo) -> None:
        self._previous_session = session

    def close_end_of_data(self) -> None:
        # Live shutdown leaves broker state to the operator; there is no
        # simulated end-of-data close for a real account.
        return None

    def flatten(self, bar: MarketBar, reason: str) -> None:
        if not self._config.flatten_enabled:
            raise TradovateOrderSafetyError("flatten_disabled")
        position = self._position
        if position is None:
            return
        self._cancel_working_orders_best_effort()
        response = self._rest_client.order_liquidate_position({
            "accountSpec": self._config.account_spec,
            "accountId": self._config.account_id,
            "symbol": bar.symbol,
            "admin": False,
        })
        order_id = str(response["orderId"])
        self._orders[order_id] = SubmittedOrder(
            order_id=order_id,
            role=ROLE_EXIT,
            side="sell" if position.side == "long" else "buy",
            quantity=position.quantity,
            symbol=bar.symbol,
            reason=reason,
        )
        self._events.append(Acked(order_id=order_id))

    def poll_events(self) -> list[BrokerEvent]:
        events = list(self._events)
        self._events.clear()
        return events

    # -- raw event ingestion ----------------------------------------------

    def ingest_raw_event(self, event: TradovateRawEvent) -> None:
        if event.kind == "position":
            self._reconcile_position_event(event.data)
            return
        if event.kind == "partial_fill":
            self._known_order(str(event.data["orderId"]))
            self._events.append(_partial_fill_from_data(event.data))
            return
        if event.kind == "fill":
            self._ingest_fill(_fill_from_data(event.data))
            return
        if event.kind == "reject":
            self._ingest_reject(event.data)
            return
        if event.kind == "cancel":
            self._ingest_cancel(event.data)
            return
        raise TradovateOrderSafetyError("unknown_tradovate_event_kind")

    def _known_order(self, order_id: str) -> SubmittedOrder:
        order = self._orders.get(order_id)
        if order is None:
            raise TradovateStateError(
                f"broker event for unknown order id {order_id} "
                "(platform liquidation or manual intervention?) -- halting"
            )
        return order

    def _ingest_fill(self, fill: Filled) -> None:
        order = self._known_order(fill.order_id)
        if order.status == "filled":
            raise TradovateStateError(f"duplicate fill for order {fill.order_id}")
        if order.status != "working":
            raise TradovateStateError(
                f"fill for {order.status} order {fill.order_id}"
            )
        order.status = "filled"
        if order.role == ROLE_ENTRY:
            self._on_entry_fill(fill, order)
        else:
            self._on_exit_fill(fill, order)
        self._events.append(fill)

    def _on_entry_fill(self, fill: Filled, order: SubmittedOrder) -> None:
        if self._position is not None:
            raise TradovateStateError(
                f"entry fill for order {fill.order_id} while a position is already open"
            )
        self._position = BrokerPosition(
            side="long" if fill.side == "buy" else "short",
            quantity=fill.quantity,
            entry_price=fill.price,
        )
        session_date = classify_timestamp(fill.timestamp_utc).session_date.isoformat()
        self._fill_ledger.open_leg(
            symbol=order.symbol,
            side=fill.side,
            quantity=fill.quantity,
            price=fill.price,
            timestamp_utc=fill.timestamp_utc,
            stop_price=order.stop_price if order.stop_price is not None else 0.0,
            session_date=session_date,
        )
        self._submit_protective_stop(fill, order)

    def _submit_protective_stop(self, fill: Filled, entry_order: SubmittedOrder) -> None:
        action = "Sell" if fill.side == "buy" else "Buy"
        body = {
            "accountSpec": self._config.account_spec,
            "accountId": self._config.account_id,
            "action": action,
            "symbol": entry_order.symbol,
            "orderQty": fill.quantity,
            "orderType": "Stop",
            "stopPrice": entry_order.stop_price,
            "isAutomated": True,
        }
        try:
            response = self._rest_client.order_place(body)
        except TradovateError as exc:
            self._emergency_flatten(entry_order.symbol)
            raise TradovateStateError(
                "protective stop submission failed; emergency flatten requested"
            ) from exc
        stop_id = str(response["orderId"])
        self._orders[stop_id] = SubmittedOrder(
            order_id=stop_id,
            role=ROLE_PROTECTIVE_STOP,
            side=action.lower(),
            quantity=fill.quantity,
            symbol=entry_order.symbol,
            stop_price=entry_order.stop_price,
            reason="stop",
        )
        self._working_stop_id = stop_id
        self._events.append(Acked(order_id=stop_id))

    def _emergency_flatten(self, symbol: str) -> None:
        # Entry-capable configs are flatten-capable by construction (__init__),
        # so no flag check here. Best-effort: the TradovateStateError raised at
        # the call site halts the loop regardless; a cancel/liquidate failure
        # leaves the account to the operator, which is exactly what halt means.
        self._cancel_working_orders_best_effort()
        try:
            response = self._rest_client.order_liquidate_position({
                "accountSpec": self._config.account_spec,
                "accountId": self._config.account_id,
                "symbol": symbol,
                "admin": False,
            })
        except TradovateError:
            return
        order_id = str(response["orderId"])
        position = self._position
        self._orders[order_id] = SubmittedOrder(
            order_id=order_id,
            role=ROLE_EXIT,
            side="sell" if position is not None and position.side == "long" else "buy",
            quantity=position.quantity if position is not None else 0,
            symbol=symbol,
            reason="emergency_flatten",
        )

    def _cancel_working_orders_best_effort(self) -> None:
        # Emergency path only: a cancel failure must not stop the liquidation.
        # Any later fill from a missed cancel is a known-id fill against an
        # impossible position state and halts through the normal guards.
        for order in list(self._orders.values()):
            if order.status != "working":
                continue
            try:
                self._rest_client.order_cancel({"orderId": int(order.order_id)})
            except TradovateError:
                continue

    def _cancel_working_stop_or_halt(self) -> None:
        stop_id = self._working_stop_id
        if stop_id is None:
            return
        try:
            self._rest_client.order_cancel({"orderId": int(stop_id)})
        except TradovateError as exc:
            # Two live closing orders must never coexist. The stop still
            # protects the position; halt for human review instead of
            # submitting the market close.
            raise TradovateStateError(
                f"failed to cancel protective stop {stop_id} before exit"
            ) from exc

    def _on_exit_fill(self, fill: Filled, order: SubmittedOrder) -> None:
        position = self._position
        if position is None:
            raise TradovateStateError(
                f"exit fill for order {fill.order_id} while flat"
            )
        closing_side = "sell" if position.side == "long" else "buy"
        if fill.side != closing_side:
            raise TradovateStateError(
                f"exit fill for order {fill.order_id} on wrong side {fill.side}"
            )
        if fill.quantity != position.quantity:
            raise TradovateStateError(
                f"exit fill quantity {fill.quantity} != position quantity "
                f"{position.quantity} (order {fill.order_id}; partial closes not modeled)"
            )
        if order.order_id == self._working_stop_id:
            self._working_stop_id = None
        self._position = None
        self._fill_ledger.close_leg(
            price=fill.price,
            timestamp_utc=fill.timestamp_utc,
            reason=order.reason or order.role,
        )

    def _ingest_reject(self, data: dict[str, Any]) -> None:
        order = self._known_order(str(data["orderId"]))
        order.status = "rejected"
        self._events.append(
            Rejected(order_id=order.order_id, reason=str(data.get("reason", "")))
        )
        if order.role == ROLE_PROTECTIVE_STOP:
            if order.order_id == self._working_stop_id:
                self._working_stop_id = None
            self._emergency_flatten(order.symbol)
            raise TradovateStateError(
                f"protective stop {order.order_id} rejected; emergency flatten requested"
            )

    def _ingest_cancel(self, data: dict[str, Any]) -> None:
        order = self._known_order(str(data["orderId"]))
        order.status = "canceled"
        if order.order_id == self._working_stop_id:
            self._working_stop_id = None
        self._events.append(Canceled(order_id=order.order_id))

    def _reconcile_position_event(self, data: dict[str, Any]) -> None:
        reported = _position_from_data(data)
        if not _positions_match(reported, self._position):
            raise TradovateStateError(
                f"broker position snapshot {reported!r} contradicts "
                f"fill-derived position {self._position!r}"
            )

    # -- account state -----------------------------------------------------

    @property
    def position(self) -> Optional[BrokerPosition]:
        return self._position

    @property
    def trades(self) -> list[Trade]:
        # STUB (gap #3) until Task 6 exposes the fill ledger's trades.
        return []

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


def _positions_match(
    reported: Optional[BrokerPosition], derived: Optional[BrokerPosition]
) -> bool:
    if reported is None or derived is None:
        return reported is None and derived is None
    # entry price is NOT compared: broker netPrice averaging legitimately
    # differs from our fill price; side+quantity define position identity.
    return reported.side == derived.side and reported.quantity == derived.quantity


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
