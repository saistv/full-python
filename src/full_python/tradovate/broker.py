"""Tradovate broker adapter.

Implements execution.broker_protocol.Broker against the Tradovate REST/WS
surface, offline-first (all tests run on fake transports). Safety model:

- Submitted-order map: every order this adapter places is recorded; any
  fill/cancel/reject for an unknown order id (platform liquidation,
  manual intervention, stale message) raises TradovateStateError, which
  subclasses ExecutionInvariantError so LiveLoop halts WITHOUT flatten
  (position truth unknown). Duplicate fills likewise halt.
- Entry authority: only a stable-flat broker state can submit an entry. A
  working entry, open position, pending exit, or recovery state rejects later
  signals before REST. Authoritative entry fills and fill-derived closed trades
  reach the strategy once through the shared feedback stream.
- Broker-held protective stop: submitted immediately on every entry fill
  at the entry's frozen stop_price, never modified afterwards (production
  policy freezes stops at entry; result.stop_updates are deliberately not
  applied, matching PositionEngine). If the stop cannot be confirmed the
  adapter flattens and raises. No OCO: the production strategy emits no
  target_price (N/A-by-design in the Failure Matrix).
- Exits: result.exits request cancellation of the working stop and wait for
  the asynchronous Canceled event before market-closing. A stop fill wins the
  race and suppresses the close. A cancel failure halts without submitting a
  second closing order. flatten() cancels working orders
  best-effort, liquidates, and registers the liquidation order so its
  fill is a known id.
- Accounting is broker truth: FillPairingLedger pairs real fills into
  models.Trade (arithmetic pinned against PositionEngine). Session P&L =
  realized net + gross unrealized at bar close (the sim's equity
  formula); daily_limit_hit uses the shared is_daily_loss_breached with
  config.daily_loss_limit; breach cancels the stop and flattens.
- Config is per-instrument: dollar_point_value has no default (NQ=20.0,
  MNQ=2.0). order_enabled requires flatten_enabled and daily_loss_limit
  at broker construction. Both live flags default False.

The 2026-07-10 gap-closure spec is historical context, not a production
readiness claim. The 2026-07-13 principal audit found additional protocol and
recovery blockers; see the 2026-07-14 broker-safe execution design.
Multi-contract live orders are prohibited until partial-fill recovery is
modeled. Any partial-fill event is therefore an invariant breach that requires
broker reconciliation before proceeding.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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
    StrategyFeedback,
)
from full_python.models import Fill, MarketBar, StrategyResult, Trade
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


class BrokerExecutionState(str, Enum):
    NORMAL = "normal"
    ENTRY_PENDING_FILL = "entry_pending_fill"
    EXIT_PENDING_CANCEL = "exit_pending_cancel"
    EXIT_PENDING_FILL = "exit_pending_fill"
    RECOVERY_REQUIRED = "recovery_required"


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


@dataclass(frozen=True)
class PendingExit:
    symbol: str
    action: str
    quantity: int
    reason: str
    stop_order_id: str


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
        self._strategy_feedback: list[StrategyFeedback] = []
        self._orders: dict[str, SubmittedOrder] = {}
        self._fill_ledger = FillPairingLedger(
            dollar_point_value=config.dollar_point_value,
            commission_per_contract_round_trip=config.commission_per_contract_round_trip,
        )
        self._position: Optional[BrokerPosition] = None
        self._working_stop_id: Optional[str] = None
        self._pending_exit: Optional[PendingExit] = None
        self._requested_cancel_ids: set[str] = set()
        self._recovery_required = False
        self._execution_state = BrokerExecutionState.NORMAL
        self._previous_session: Optional[SessionInfo] = None
        self._daily_limit_hit = False

    # -- per-bar hooks (LiveLoop sequence) --------------------------------

    def process_bar_open(self, bar: MarketBar, session: SessionInfo) -> float:
        self._handle_session_rollover(session)
        self._fill_ledger.mark_bar(high=bar.high, low=bar.low)
        session_pnl = self._session_pnl(bar, session)
        if not self._daily_limit_hit and is_daily_loss_breached(
            session_pnl, self._config.daily_loss_limit
        ):
            self._daily_limit_hit = True
            if self._position is not None:
                if not self._config.flatten_enabled:
                    raise TradovateStateError(
                        "daily loss limit breached with flatten_enabled=False"
                    )
                self.flatten(bar, "daily_limit")
        return session_pnl

    def _handle_session_rollover(self, session: SessionInfo) -> None:
        previous = self._previous_session
        if previous is None or session.session_date == previous.session_date:
            return
        if self._position is not None or self._has_working_orders():
            raise TradovateStateError(
                "session rollover with an open position or working orders -- "
                "the 15:59 backstop should have flattened; halting for review"
            )
        self._daily_limit_hit = False

    def _has_working_orders(self) -> bool:
        return any(order.status == "working" for order in self._orders.values())

    def _session_pnl(self, bar: MarketBar, session: SessionInfo) -> float:
        # Same equity formula as the sim: realized NET since session start
        # plus GROSS unrealized at the bar close (Pine's strategy.equity --
        # openprofit excludes the open trade's commission).
        realized = self._fill_ledger.realized_session_pnl(
            session.session_date.isoformat()
        )
        unrealized = 0.0
        position = self._position
        if position is not None:
            direction = 1 if position.side == "long" else -1
            unrealized = (
                (bar.close - position.entry_price)
                * direction
                * float(self._config.dollar_point_value)
                * position.quantity
            )
        return realized + unrealized

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
            if self._pending_exit is not None:
                continue
            stop_id = self._working_stop_id
            if stop_id is None:
                raise TradovateStateError(
                    "strategy exit requested for an unprotected open position"
                )
            self._pending_exit = PendingExit(
                symbol=bar.symbol,
                action="Sell" if self._position.side == "long" else "Buy",
                quantity=self._position.quantity,
                reason=exit_decision.reason,
                stop_order_id=stop_id,
            )
            self._execution_state = BrokerExecutionState.EXIT_PENDING_CANCEL
            self._request_cancel_or_halt(stop_id)
        for intent in result.order_intents:
            if not self._config.order_enabled:
                self._events.append(Rejected(order_id="", reason="order_disabled"))
                continue
            if "stop_price" not in intent.metadata:
                raise TradovateOrderSafetyError("stop_price metadata required")
            if intent.quantity != 1:
                raise TradovateOrderSafetyError(
                    "live quantity must equal 1 until partial-fill recovery is modeled"
                )
            if not self._entry_is_stable_flat():
                self._events.append(
                    Rejected(order_id="", reason="entry_not_stable_flat")
                )
                continue
            body = {
                "accountSpec": self._config.account_spec,
                "accountId": self._config.account_id,
                "action": _action_from_side(intent.side),
                "symbol": intent.symbol,
                "orderQty": intent.quantity,
                "orderType": "Market",
                "isAutomated": True,
            }
            try:
                response = self._rest_client.order_place(body)
            except TradovateError as exc:
                self._recovery_required = True
                self._execution_state = BrokerExecutionState.RECOVERY_REQUIRED
                raise TradovateStateError(
                    "entry submission outcome unknown; broker reconciliation required"
                ) from exc
            order_id = self._order_id_or_reject(response, role=ROLE_ENTRY)
            if order_id is None:
                continue
            self._orders[order_id] = SubmittedOrder(
                order_id=order_id,
                role=ROLE_ENTRY,
                side=intent.side.lower(),
                quantity=intent.quantity,
                symbol=intent.symbol,
                stop_price=float(intent.metadata["stop_price"]),
                reason=intent.reason,
            )
            self._execution_state = BrokerExecutionState.ENTRY_PENDING_FILL
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
        self._recovery_required = True
        self._execution_state = BrokerExecutionState.RECOVERY_REQUIRED
        self._cancel_working_orders_best_effort()
        position = self._position
        if position is None:
            return
        try:
            response = self._rest_client.order_liquidate_position({
                "accountSpec": self._config.account_spec,
                "accountId": self._config.account_id,
                "symbol": bar.symbol,
                "admin": False,
            })
        except TradovateError as exc:
            raise TradovateStateError(
                "liquidation submission outcome unknown; broker reconciliation required"
            ) from exc
        order_id = self._required_order_id(response, "liquidation")
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

    def poll_strategy_feedback(self) -> list[StrategyFeedback]:
        feedback = list(self._strategy_feedback)
        self._strategy_feedback.clear()
        return feedback

    # -- raw event ingestion ----------------------------------------------

    def ingest_raw_event(self, event: TradovateRawEvent) -> None:
        if event.kind == "position":
            self._reconcile_position_event(event.data)
            return
        if event.kind == "partial_fill":
            order = self._known_order(str(event.data["orderId"]))
            self._events.append(_partial_fill_from_data(event.data))
            raise TradovateStateError(
                f"partial fill for order {order.order_id}; broker reconciliation required"
            )
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
        if order.order_id in self._requested_cancel_ids and self._recovery_required:
            self._emergency_flatten(order.symbol)
            raise TradovateStateError(
                f"entry order {order.order_id} filled after flatten cancellation; "
                "emergency flatten requested"
            )
        self._submit_protective_stop(fill, order)
        if not self._recovery_required:
            self._execution_state = BrokerExecutionState.NORMAL
        self._strategy_feedback.append(Fill(
            timestamp_utc=fill.timestamp_utc,
            symbol=order.symbol,
            side=fill.side,
            quantity=fill.quantity,
            price=fill.price,
            reason=order.reason,
            metadata={"broker_order_id": fill.order_id},
        ))

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
        try:
            stop_id = self._required_order_id(response, "protective stop")
        except TradovateStateError:
            self._emergency_flatten(entry_order.symbol)
            raise
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
        self._recovery_required = True
        self._execution_state = BrokerExecutionState.RECOVERY_REQUIRED
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
        try:
            order_id = self._required_order_id(response, "emergency liquidation")
        except TradovateStateError:
            return
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
                self._requested_cancel_ids.add(order.order_id)
            except TradovateError:
                continue

    def _request_cancel_or_halt(self, stop_id: str) -> None:
        try:
            self._rest_client.order_cancel({"orderId": int(stop_id)})
        except TradovateError as exc:
            # Two live closing orders must never coexist. The stop still
            # protects the position; halt for human review instead of
            # submitting the market close.
            self._pending_exit = None
            self._execution_state = BrokerExecutionState.RECOVERY_REQUIRED
            raise TradovateStateError(
                f"failed to cancel protective stop {stop_id} before exit"
            ) from exc
        self._requested_cancel_ids.add(stop_id)

    def _submit_pending_exit(self) -> None:
        pending = self._pending_exit
        if pending is None:
            return
        body = {
            "accountSpec": self._config.account_spec,
            "accountId": self._config.account_id,
            "action": pending.action,
            "symbol": pending.symbol,
            "orderQty": pending.quantity,
            "orderType": "Market",
            "isAutomated": True,
        }
        try:
            response = self._rest_client.order_place(body)
        except TradovateError as exc:
            self._pending_exit = None
            self._execution_state = BrokerExecutionState.RECOVERY_REQUIRED
            raise TradovateStateError(
                "exit submission outcome unknown after stop cancellation; "
                "broker reconciliation required"
            ) from exc
        try:
            order_id = self._required_order_id(response, "strategy exit")
        except TradovateStateError:
            self._pending_exit = None
            self._emergency_flatten(pending.symbol)
            raise
        self._register_order(SubmittedOrder(
            order_id=order_id,
            role=ROLE_EXIT,
            side=pending.action.lower(),
            quantity=pending.quantity,
            symbol=pending.symbol,
            reason=pending.reason,
        ))
        self._pending_exit = None
        self._execution_state = BrokerExecutionState.EXIT_PENDING_FILL
        self._events.append(Acked(order_id=order_id))

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
        if (
            order.role == ROLE_PROTECTIVE_STOP
            and self._pending_exit is not None
            and self._pending_exit.stop_order_id == order.order_id
        ):
            self._pending_exit = None
            self._requested_cancel_ids.discard(order.order_id)
        self._position = None
        if not self._recovery_required:
            self._execution_state = BrokerExecutionState.NORMAL
        trade = self._fill_ledger.close_leg(
            price=fill.price,
            timestamp_utc=fill.timestamp_utc,
            reason=order.reason or order.role,
        )
        self._strategy_feedback.append(trade)

    def _ingest_reject(self, data: dict[str, Any]) -> None:
        order = self._known_order(str(data["orderId"]))
        order.status = "rejected"
        self._events.append(
            Rejected(order_id=order.order_id, reason=str(data.get("reason", "")))
        )
        if order.role == ROLE_ENTRY:
            if not self._recovery_required:
                self._execution_state = BrokerExecutionState.NORMAL
            return
        if order.role == ROLE_PROTECTIVE_STOP:
            if order.order_id == self._working_stop_id:
                self._working_stop_id = None
            self._emergency_flatten(order.symbol)
            raise TradovateStateError(
                f"protective stop {order.order_id} rejected; emergency flatten requested"
            )
        if order.role == ROLE_EXIT:
            if order.reason != "emergency_flatten":
                self._emergency_flatten(order.symbol)
            raise TradovateStateError(
                f"exit order {order.order_id} rejected; recovery required"
            )

    def _ingest_cancel(self, data: dict[str, Any]) -> None:
        order = self._known_order(str(data["orderId"]))
        requested = order.order_id in self._requested_cancel_ids
        self._requested_cancel_ids.discard(order.order_id)
        order.status = "canceled"
        if order.order_id == self._working_stop_id:
            self._working_stop_id = None
        self._events.append(Canceled(order_id=order.order_id))
        if order.role == ROLE_ENTRY:
            if not self._recovery_required:
                self._execution_state = BrokerExecutionState.NORMAL
            return
        if order.role == ROLE_PROTECTIVE_STOP and self._position is not None:
            pending = self._pending_exit
            if requested and pending is not None and pending.stop_order_id == order.order_id:
                self._submit_pending_exit()
                return
            if requested and self._recovery_required:
                return
            self._emergency_flatten(order.symbol)
            raise TradovateStateError(
                f"protective stop {order.order_id} canceled unexpectedly; "
                "emergency flatten requested"
            )

    def _order_id_or_reject(self, response: Any, *, role: str) -> Optional[str]:
        if isinstance(response, dict) and response.get("orderId") is not None:
            order_id = str(response["orderId"])
            if order_id in self._orders:
                raise TradovateStateError(f"duplicate broker order id {order_id}")
            return order_id
        reason = response.get("failureReason") if isinstance(response, dict) else None
        if role == ROLE_ENTRY and reason:
            self._events.append(Rejected(order_id="", reason=str(reason)))
            return None
        raise TradovateStateError(
            f"{role} response missing orderId: {response!r}"
        )

    def _required_order_id(self, response: Any, operation: str) -> str:
        order_id = self._order_id_or_reject(response, role=operation)
        if order_id is None:  # only entry responses can return None
            raise TradovateStateError(f"{operation} response rejected")
        return order_id

    def _register_order(self, order: SubmittedOrder) -> None:
        if order.order_id in self._orders:
            raise TradovateStateError(f"duplicate broker order id {order.order_id}")
        self._orders[order.order_id] = order

    def _entry_is_stable_flat(self) -> bool:
        return (
            self._execution_state == BrokerExecutionState.NORMAL
            and not self._recovery_required
            and self._position is None
            and self._pending_exit is None
            and not self._has_working_orders()
        )

    def _reconcile_position_event(self, data: dict[str, Any]) -> None:
        reported = _position_from_data(data)
        if not _positions_match(reported, self._position):
            raise TradovateStateError(
                f"broker position snapshot {reported!r} contradicts "
                f"fill-derived position {self._position!r}"
            )

    def reconcile_rest_positions(self, positions: list) -> None:
        """Cross-check a REST /position/list snapshot against fill-derived
        truth (Failure Matrix: REST vs WebSocket disagreement -> halt).

        Assumes a single instrument with at most one open contract
        position at a time. A compliant snapshot therefore has at most
        one item with a nonzero ``netPos``. If more than one item
        reports a nonzero ``netPos`` (e.g. a contract-roll straddle
        holding both the old and new contract, or a duplicated/
        contradictory feed), this halts rather than summing them --
        a +1/-1 pair must never be netted down to a false flat.

        ``entry_price`` in the mismatch message below is diagnostic
        only: it is the last non-null ``netPrice`` seen while scanning
        the snapshot, never a value that is compared. ``_positions_match``
        compares side + quantity only -- broker netPrice averaging
        legitimately differs from our fill price.
        """
        open_items = [item for item in positions if int(item.get("netPos", 0)) != 0]
        if len(open_items) > 1:
            raise TradovateStateError(
                "REST position snapshot reports multiple open contract "
                f"positions {open_items!r} -- cannot reconcile a single-"
                "instrument position from this (e.g. a roll straddle); halting"
            )
        net = 0
        price = 0.0
        for item in positions:
            net += int(item.get("netPos", 0))
            if item.get("netPrice") is not None:
                price = float(item["netPrice"])
        reported: Optional[BrokerPosition] = None
        if net != 0:
            reported = BrokerPosition(
                side="long" if net > 0 else "short",
                quantity=abs(net),
                entry_price=price,
            )
        if not _positions_match(reported, self._position):
            raise TradovateStateError(
                f"REST position snapshot {reported!r} contradicts "
                f"fill-derived position {self._position!r}"
            )

    # -- account state -----------------------------------------------------

    @property
    def position(self) -> Optional[BrokerPosition]:
        return self._position

    @property
    def execution_state(self) -> BrokerExecutionState:
        return self._execution_state

    @property
    def trades(self) -> list[Trade]:
        return self._fill_ledger.trades

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


def _position_from_data(data: dict[str, Any]) -> Optional[BrokerPosition]:
    side = data.get("side")
    qty = data.get("qty", data.get("netPos"))
    if side == "flat" or qty == 0:
        return None
    if side not in {"long", "short"}:
        raise TradovateOrderSafetyError("unsupported_position_side")
    price = data.get("price", data.get("entryPrice"))
    if price is None:
        raise TradovateOrderSafetyError("position_price_required")
    return BrokerPosition(side=side, quantity=int(qty), entry_price=float(price))


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
