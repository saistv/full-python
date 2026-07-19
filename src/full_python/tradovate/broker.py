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
- Financial intents: every mutating REST request requires an injected durable
  intent journal. Pending state is durable before POST, acknowledged broker IDs
  are durable before volatile mapping, and ambiguous outcomes latch recovery.
  Any journal history on restart remains closed pending Slice D hydration.
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

import uuid
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
from full_python.execution.order_intent_journal import (
    IntentJournal,
    IntentState,
)
from full_python.models import Fill, MarketBar, StrategyResult, Trade
from full_python.risk.daily_loss import is_daily_loss_breached
from full_python.risk.limits import RiskLimits
from full_python.risk.risk_manager import RiskManager
from full_python.tradovate.config import TradovateAdapterConfig
from full_python.tradovate.account_sync import AccountHydrationSnapshot
from full_python.tradovate.errors import (
    TradovateOrderRejectedError,
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
# Working orders inherited from a previous run (P1-8). Never adopted for
# trading: they exist only so the startup flatten can cancel them and
# recognize their late fills.
ROLE_INHERITED = "inherited"


class BrokerExecutionState(str, Enum):
    NORMAL = "normal"
    ENTRY_PENDING_FILL = "entry_pending_fill"
    EXIT_PENDING_CANCEL = "exit_pending_cancel"
    EXIT_PENDING_FILL = "exit_pending_fill"
    FLATTEN_PENDING_CANCEL = "flatten_pending_cancel"
    FLATTEN_PENDING_FILL = "flatten_pending_fill"
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
    logical_intent_id: str = ""
    status: str = "working"  # "working" | "filled" | "canceled" | "rejected"


@dataclass(frozen=True)
class PendingExit:
    symbol: str
    action: str
    quantity: int
    reason: str
    stop_order_id: str


@dataclass(frozen=True)
class PendingFlatten:
    """A staged flatten: cancel confirmed first, liquidate second (P0-2).

    Resolution (confirmed flat with no working orders) must land within the
    request bar; `process_bar_open` halts on any later bar (P0-04 deadline).
    """

    reason: str
    awaiting_cancel_ids: frozenset
    requested_on_bar: str


class TradovateBroker:
    def __init__(
        self,
        config: TradovateAdapterConfig,
        rest_client: Any,
        *,
        intent_journal: Optional[IntentJournal] = None,
        risk_limits: Optional[RiskLimits] = None,
    ) -> None:
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
        if config.flatten_enabled:
            if config.contract_symbol is None:
                raise TradovateConfigError(
                    "flatten_enabled requires an exact contract_symbol"
                )
            if config.contract_id is None:
                raise TradovateConfigError(
                    "flatten_enabled requires an exact contract_id"
                )
            if intent_journal is None:
                raise TradovateConfigError(
                    "flatten_enabled requires a durable intent_journal"
                )
        if config.order_enabled and risk_limits is None:
            raise TradovateConfigError(
                "order_enabled requires risk_limits -- the shared sim/live "
                "RiskManager veto (audit P1-7): live must refuse every "
                "order the simulator refuses"
            )
        self._config = config
        self._rest_client = rest_client
        self._intent_journal = intent_journal
        self._risk_manager = (
            RiskManager(risk_limits) if risk_limits is not None else None
        )
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
        self._pending_flatten: Optional[PendingFlatten] = None
        self._pending_flatten_liquidation_id: Optional[str] = None
        self._requested_cancel_ids: set[str] = set()
        self._cancel_intent_ids: dict[str, str] = {}
        self._liquidation_in_flight = False
        # Empty local memory is not evidence that a real account is flat. Every
        # order-capable broker starts closed until user sync and REST agree.
        self._recovery_required = bool(
            config.order_enabled
            or (intent_journal is not None and intent_journal.has_history)
        )
        self._execution_state = (
            BrokerExecutionState.RECOVERY_REQUIRED
            if self._recovery_required
            else BrokerExecutionState.NORMAL
        )
        self._previous_session: Optional[SessionInfo] = None
        self._daily_limit_hit = False
        self._account_realized_pnl = 0.0
        self._hydrated_trade_date: Optional[str] = None

    def _require_snapshot_identity(self, snapshot: AccountHydrationSnapshot) -> None:
        if snapshot.account_id != self._config.account_id:
            self._latch_recovery()
            raise TradovateStateError("hydration snapshot account identity mismatch")
        if snapshot.account_spec != self._config.account_spec:
            self._latch_recovery()
            raise TradovateStateError("hydration snapshot account name mismatch")
        if snapshot.contract_id != self._active_contract_id():
            self._latch_recovery()
            raise TradovateStateError("hydration snapshot contract identity mismatch")
        if snapshot.contract_symbol != self._active_contract_symbol():
            self._latch_recovery()
            raise TradovateStateError("hydration snapshot contract symbol mismatch")

    def hydrate_account_state(self, snapshot: AccountHydrationSnapshot) -> None:
        """Open the entry latch only from exact, reconciled stable-flat truth."""
        self._require_snapshot_identity(snapshot)

        local_realized = self._fill_ledger.realized_session_pnl(
            snapshot.trade_date
        )
        self._account_realized_pnl = snapshot.daily_realized_pnl - local_realized
        self._hydrated_trade_date = snapshot.trade_date
        self._daily_limit_hit = is_daily_loss_breached(
            snapshot.daily_realized_pnl,
            self._config.daily_loss_limit,
        )
        if snapshot.position is not None:
            self._position = snapshot.position
            self._latch_recovery()
            raise TradovateStateError(
                "inherited open position requires strategy-state recovery"
            )
        if snapshot.working_orders:
            self._latch_recovery()
            raise TradovateStateError(
                "inherited working orders require order-state recovery"
            )
        if not snapshot.entry_permitted:
            self._latch_recovery()
            raise TradovateStateError("hydration snapshot does not permit entry")

        journal = self._intent_journal
        if journal is not None:
            for record in list(journal.latest_by_intent.values()):
                if record.state in {
                    IntentState.REJECTED,
                    IntentState.CONFIRMED,
                    IntentState.RECONCILED,
                }:
                    continue
                if record.state == IntentState.REQUEST_ACCEPTED:
                    client_operation_id = record.client_operation_id
                    command = (
                        None
                        if client_operation_id is None
                        else snapshot.commands_by_client_id.get(
                            client_operation_id
                        )
                    )
                    order_id = (
                        None
                        if command is None or command.get("orderId") is None
                        else str(command["orderId"])
                    )
                    order = (
                        None
                        if order_id is None
                        else snapshot.orders_by_id.get(order_id)
                    )
                    if (
                        record.role != "cancel"
                        or command is None
                        or command.get("isAutomated") is not True
                        or order is None
                        or str(order.get("ordStatus"))
                        not in {"Canceled", "Expired", "Filled"}
                        # "Filled": a cancel that raced and LOST to a fill is
                        # legitimately terminal (review 2026-07-19 P1-3).
                    ):
                        self._latch_recovery()
                        raise TradovateStateError(
                            f"accepted cancel intent {record.intent_id} is not "
                            "confirmed terminal by its broker command"
                        )
                    journal.transition(
                        record.intent_id,
                        IntentState.RECONCILED,
                        broker_order_id=order_id,
                        detail=f"startup:{order.get('ordStatus')}",
                    )
                    continue
                if record.state == IntentState.ACKNOWLEDGED:
                    client_operation_id = record.client_operation_id
                    if client_operation_id is None:
                        self._latch_recovery()
                        raise TradovateStateError(
                            f"acknowledged intent {record.intent_id} has no "
                            "broker-visible client operation ID"
                        )
                    command = snapshot.commands_by_client_id.get(
                        client_operation_id
                    )
                    if command is None:
                        self._latch_recovery()
                        raise TradovateStateError(
                            f"acknowledged intent {record.intent_id} has no "
                            "matching broker command"
                        )
                    order_id = record.broker_order_id
                    if (
                        str(command.get("orderId")) != order_id
                        or command.get("isAutomated") is not True
                    ):
                        self._latch_recovery()
                        raise TradovateStateError(
                            f"acknowledged intent {record.intent_id} broker command "
                            "does not match its order or automation authority"
                        )
                    order = None if order_id is None else snapshot.orders_by_id.get(order_id)
                    if order is None or str(order.get("ordStatus")) in {
                        "PendingCancel", "PendingNew", "PendingReplace",
                        "Suspended", "Unknown", "Working",
                    }:
                        self._latch_recovery()
                        raise TradovateStateError(
                            f"acknowledged intent {record.intent_id} is not terminal "
                            "in the broker snapshot"
                        )
                    journal.transition(
                        record.intent_id,
                        IntentState.RECONCILED,
                        broker_order_id=order_id,
                        detail=f"startup:{order.get('ordStatus')}",
                    )
                    continue
                self._latch_recovery()
                raise TradovateStateError(
                    f"journal intent {record.intent_id} is not safely reconcilable"
                )

        for order_id, local_order in self._orders.items():
            broker_order = snapshot.orders_by_id.get(order_id)
            if broker_order is not None:
                local_order.status = str(broker_order.get("ordStatus", "")).lower()
        self._pending_exit = None
        self._working_stop_id = None
        self._requested_cancel_ids.clear()
        self._cancel_intent_ids.clear()
        self._liquidation_in_flight = False
        self._position = None
        self._recovery_required = False
        self._execution_state = BrokerExecutionState.NORMAL

    def invalidate_account_state(self, reason: str) -> None:
        """Close account authority before any uncertain state transition."""
        if not isinstance(reason, str) or not reason.strip():
            raise TradovateStateError("account state invalidation requires a reason")
        self._hydrated_trade_date = None
        self._account_realized_pnl = 0.0
        self._latch_recovery()

    # -- per-bar hooks (LiveLoop sequence) --------------------------------

    def process_bar_open(self, bar: MarketBar, session: SessionInfo) -> float:
        self._handle_session_rollover(session)
        pending_flatten = self._pending_flatten
        if (
            pending_flatten is not None
            and pending_flatten.requested_on_bar != bar.timestamp_utc
        ):
            # One full bar is the confirmation deadline: every cancel/fill
            # confirmation for a marketable order arrives within the same
            # one-minute bar on this feed. Anything slower halts for review
            # (the raise reaches LiveLoop, which writes the durable
            # execution_halt ledger entry -- the external alert).
            self._latch_recovery()
            raise TradovateStateError(
                f"unresolved flatten ({pending_flatten.reason}) from bar "
                f"{pending_flatten.requested_on_bar}; halting for review"
            )
        self._require_current_hydration(session)
        self._fill_ledger.mark_bar(high=bar.high, low=bar.low)
        close_minutes = session.rth_close_minutes_et
        if (
            close_minutes is not None
            and session.minutes_from_midnight_et >= close_minutes - 1
            and self._pending_flatten is None
            and not self._liquidation_in_flight
            and (self._position is not None or self._has_working_orders())
        ):
            # Broker-side session-close backstop from the exchange calendar
            # (P0-03): fires at close-1 on EVERY session, including early
            # closes, independent of the strategy's own backstop exit.
            if not self._config.flatten_enabled:
                raise TradovateStateError(
                    "session close reached with an open position and "
                    "flatten_enabled=False"
                )
            self.flatten(bar, "session_close_backstop")
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
                "the session-close backstop should have flattened; halting for review"
            )
        if self._config.order_enabled:
            if self._hydrated_trade_date != session.session_date.isoformat():
                self._latch_recovery()
                raise TradovateStateError(
                    "session rollover requires fresh broker account hydration"
                )
            return
        self._daily_limit_hit = False

    def _has_working_orders(self) -> bool:
        return any(order.status == "working" for order in self._orders.values())

    def _require_current_hydration(self, session: SessionInfo) -> None:
        if (
            self._config.order_enabled
            and self._hydrated_trade_date != session.session_date.isoformat()
        ):
            self._latch_recovery()
            raise TradovateStateError(
                "broker account hydration does not match the active session"
            )

    def _session_pnl(self, bar: MarketBar, session: SessionInfo) -> float:
        # Same equity formula as the sim: realized NET since session start
        # plus GROSS unrealized at the bar close (Pine's strategy.equity --
        # openprofit excludes the open trade's commission).
        realized = self._account_realized_pnl + self._fill_ledger.realized_session_pnl(
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
            self._require_active_symbol(exit_decision.symbol)
            if self._pending_flatten is not None or self._liquidation_in_flight:
                # Review 2026-07-19 P0-2B: the flatten owns the close; a
                # same-bar strategy exit must not start a second closing path
                # or re-request the same cancel.
                self._events.append(
                    Rejected(order_id="", reason="flatten_in_progress")
                )
                continue
            if self._pending_exit is not None:
                continue
            stop_id = self._working_stop_id
            if stop_id is None:
                raise TradovateStateError(
                    "strategy exit requested for an unprotected open position"
                )
            self._pending_exit = PendingExit(
                symbol=self._active_contract_symbol(),
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
            self._require_active_symbol(intent.symbol)
            if "stop_price" not in intent.metadata:
                raise TradovateOrderSafetyError("stop_price metadata required")
            if intent.quantity != 1:
                raise TradovateOrderSafetyError(
                    "live quantity must equal 1 until partial-fill recovery is modeled"
                )
            if self._risk_manager is not None:
                # The exact veto the simulator applies (audit P1-7): identical
                # module, identical reason strings, evaluated before any
                # journal or REST activity. Malformed strategy output (missing
                # stop, quantity != 1) stays a LOUD TradovateOrderSafetyError
                # above -- those are code bugs, not market conditions.
                veto = self._risk_manager.veto_reason(
                    has_open_order=(
                        self._position is not None
                        or self._pending_exit is not None
                        or self._pending_flatten is not None
                        or self._liquidation_in_flight
                        or self._has_working_orders()
                    ),
                    daily_limit_hit=self._daily_limit_hit,
                    session=session,
                    intent=intent,
                    reference_price=float(
                        intent.metadata.get("signal_price", bar.close)
                    ),
                )
                if veto is not None:
                    self._events.append(Rejected(order_id="", reason=veto))
                    continue
            if not self._entry_is_stable_flat():
                self._events.append(
                    Rejected(order_id="", reason="entry_not_stable_flat")
                )
                continue
            self._require_current_hydration(session)
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
                receipt = self._journaled_order_place(body, role=ROLE_ENTRY)
            except TradovateStateError:
                raise
            except TradovateError as exc:
                raise TradovateStateError(
                    "entry submission outcome unknown; broker reconciliation required"
                ) from exc
            if receipt is None:
                continue
            order_id, logical_intent_id = receipt
            self._register_order(SubmittedOrder(
                order_id=order_id,
                role=ROLE_ENTRY,
                side=intent.side.lower(),
                quantity=intent.quantity,
                symbol=intent.symbol,
                stop_price=float(intent.metadata["stop_price"]),
                reason=intent.reason,
                logical_intent_id=logical_intent_id,
            ))
            self._execution_state = BrokerExecutionState.ENTRY_PENDING_FILL
            self._events.append(Acked(order_id=order_id))

    def note_bar_processed(self, bar: MarketBar, session: SessionInfo) -> None:
        self._previous_session = session

    def close_end_of_data(self) -> None:
        # Live shutdown leaves broker state to the operator; there is no
        # simulated end-of-data close for a real account.
        return None

    def flatten(self, bar: MarketBar, reason: str) -> None:
        """Staged, event-confirmed flatten (P0-2/P0-04/P1-5).

        Cancel every working order first and wait for confirmed cancellation
        before any liquidation, so two live closing orders can never coexist.
        A routine flatten does NOT latch recovery; only unresolved outcomes do.
        """
        if not self._config.flatten_enabled:
            raise TradovateOrderSafetyError("flatten_disabled")
        if self._pending_flatten is not None or self._liquidation_in_flight:
            return
        if self._position is None and not self._has_working_orders():
            return  # routine no-op: nothing to cancel, nothing to close
        self._begin_flatten(reason, requested_on_bar=bar.timestamp_utc)

    @property
    def flatten_in_progress(self) -> bool:
        return self._pending_flatten is not None or self._liquidation_in_flight

    def startup_flatten(
        self, snapshot: AccountHydrationSnapshot, *, timestamp_utc: str
    ) -> None:
        """Close inherited state via the confirmed-flatten protocol (P1-8).

        Operator policy (2026-07-19): an inherited position or working order
        set is FLATTENED, never adopted for trading. Resolution deliberately
        stays RECOVERY_REQUIRED; entries reopen only through a fresh
        stable-flat hydration against new sync+REST agreement.
        """
        if not self._config.flatten_enabled:
            raise TradovateStateError(
                "inherited state requires flatten_enabled for the startup flatten"
            )
        self._require_snapshot_identity(snapshot)
        if self._pending_flatten is not None or self._liquidation_in_flight:
            return
        if snapshot.position is None and not snapshot.working_orders:
            raise TradovateStateError(
                "startup flatten called for a stable-flat snapshot; "
                "use hydrate_account_state"
            )
        # Review 2026-07-19 P0-4: the supported inherited state space is
        # enforced HERE, not discovered mid-race. Multi-contract partial
        # lifecycle is deferred; an order that could create or increase
        # exposure has no safe automated close.
        position = snapshot.position
        if position is not None and position.quantity != 1:
            self._latch_recovery()
            raise TradovateStateError(
                f"inherited position quantity {position.quantity} requires "
                "MANUAL flatten -- multi-contract partial-fill lifecycle is "
                "deferred"
            )
        reducing_side = (
            None if position is None
            else ("sell" if position.side == "long" else "buy")
        )
        for row in snapshot.working_orders:
            side = str(row.get("action") or "").lower()
            if reducing_side is None or side != reducing_side:
                self._latch_recovery()
                raise TradovateStateError(
                    f"inherited working order {row.get('id')!r} could create "
                    "or increase exposure; manual intervention required"
                )
        for row in snapshot.working_orders:
            order_id = str(row.get("id"))
            if order_id in self._orders:
                continue
            self._register_order(SubmittedOrder(
                order_id=order_id,
                role=ROLE_INHERITED,
                side=str(row.get("action") or "").lower(),
                quantity=int(row.get("orderQty") or 0),
                symbol=self._active_contract_symbol(),
                reason="inherited",
            ))
        self._position = snapshot.position
        self._begin_flatten(
            "inherited_state_flatten", requested_on_bar=timestamp_utc
        )

    def _begin_flatten(self, reason: str, *, requested_on_bar: str) -> None:
        working = [o for o in self._orders.values() if o.status == "working"]
        to_cancel = []
        for order in working:
            if order.order_id in self._requested_cancel_ids:
                to_cancel.append(order.order_id)
                continue
            try:
                self._journaled_cancel(order.order_id)
            except Exception as exc:
                # Two live closing orders must never coexist (P0-2). The
                # working orders still stand; halt for review instead of
                # liquidating blind.
                self._latch_recovery()
                raise TradovateStateError(
                    f"flatten could not cancel working order {order.order_id}; "
                    "halting with existing protection in place"
                ) from exc
            self._requested_cancel_ids.add(order.order_id)
            to_cancel.append(order.order_id)
        self._pending_flatten = PendingFlatten(
            reason=reason,
            awaiting_cancel_ids=frozenset(to_cancel),
            requested_on_bar=requested_on_bar,
        )
        if to_cancel:
            self._execution_state = BrokerExecutionState.FLATTEN_PENDING_CANCEL
            return
        # Position with no working orders: liquidate directly, still confirmed.
        self._submit_flatten_liquidation()

    def _submit_flatten_liquidation(self) -> None:
        pending = self._pending_flatten
        if pending is None:
            return
        position = self._position
        if position is None:
            # Working orders canceled and no position remains: flat achieved.
            self._resolve_pending_flatten()
            return
        body = {
            "accountId": self._config.account_id,
            "contractId": self._active_contract_id(),
            "admin": False,
            "isAutomated": True,
        }
        self._liquidation_in_flight = True
        try:
            receipt = self._journaled_liquidation(body)
        except TradovateOrderRejectedError:
            # Review 2026-07-19 P0-3: definitively rejected -- no order
            # exists. Clear the latches so a later explicit flatten retry is
            # possible after operator review; still halt now.
            self._liquidation_in_flight = False
            self._pending_flatten = None
            self._pending_flatten_liquidation_id = None
            self._latch_recovery()
            raise
        except TradovateStateError:
            # Unknown outcome: the liquidation MAY exist at the broker.
            # Keep the in-flight latch -- a retry could double-close.
            self._latch_recovery()
            raise
        except Exception as exc:
            self._latch_recovery()
            raise TradovateStateError(
                "liquidation submission outcome unknown; broker reconciliation required"
            ) from exc
        if receipt is None:
            # Unreachable for the liquidation role (rejection raises), kept
            # as a defensive guard against interpreter changes.
            self._latch_recovery()
            raise TradovateStateError("liquidation returned no receipt")
        order_id, logical_intent_id = receipt
        self._register_order(SubmittedOrder(
            order_id=order_id,
            role=ROLE_EXIT,
            side="sell" if position.side == "long" else "buy",
            quantity=position.quantity,
            symbol=self._active_contract_symbol(),
            reason=pending.reason,
            logical_intent_id=logical_intent_id,
        ))
        self._pending_flatten_liquidation_id = order_id
        self._execution_state = BrokerExecutionState.FLATTEN_PENDING_FILL
        self._events.append(Acked(order_id=order_id))

    def _resolve_pending_flatten(self) -> None:
        """Confirm flat + no working orders before leaving the flatten (P0-04)."""
        self._liquidation_in_flight = False
        pending = self._pending_flatten
        self._pending_flatten = None
        self._pending_flatten_liquidation_id = None
        # Review 2026-07-19 P1-3: the flatten consumed every close path; a
        # stale pending exit would make the resolved NORMAL state unusable
        # (its next entry vetoes position_already_open).
        self._pending_exit = None
        if pending is None:
            return
        if self._position is not None:
            self._latch_recovery()
            raise TradovateStateError(
                "flatten resolution with a position still open; recovery required"
            )
        if self._has_working_orders():
            self._latch_recovery()
            raise TradovateStateError(
                "flatten resolution with a residual working order; recovery required"
            )
        if not self._recovery_required:
            self._execution_state = BrokerExecutionState.NORMAL
        else:
            # A latched resolve (the P1-8 startup flatten) must not leave a
            # stale FLATTEN_* state behind.
            self._execution_state = BrokerExecutionState.RECOVERY_REQUIRED

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
            self._require_event_identity(event.data, source="partial fill")
            order = self._known_order(str(event.data["orderId"]))
            self._events.append(_partial_fill_from_data(event.data))
            raise TradovateStateError(
                f"partial fill for order {order.order_id}; broker reconciliation required"
            )
        if event.kind == "fill":
            self._require_event_identity(event.data, source="fill")
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
        if order.role == ROLE_INHERITED and self._position is None:
            # Review 2026-07-19 P0-4A (defense in depth behind the boundary
            # validation): an inherited order filling while locally flat has
            # just created REAL exposure. Adopt it so the emergency
            # liquidation covers it, then halt.
            self._position = BrokerPosition(
                side="long" if fill.side == "buy" else "short",
                quantity=fill.quantity,
                entry_price=fill.price,
            )
            self._emergency_flatten()
            raise TradovateStateError(
                f"inherited order {fill.order_id} filled while locally flat; "
                "emergency flatten requested"
            )
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
        if order.order_id in self._requested_cancel_ids and (
            self._recovery_required or self._pending_flatten is not None
        ):
            self._emergency_flatten()
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
            receipt = self._journaled_order_place(body, role=ROLE_PROTECTIVE_STOP)
        except TradovateStateError:
            self._emergency_flatten()
            raise
        except Exception as exc:
            self._emergency_flatten()
            raise TradovateStateError(
                "protective stop submission failed; emergency flatten requested"
            ) from exc
        if receipt is None:
            self._emergency_flatten()
            raise TradovateStateError("protective stop response rejected")
        stop_id, logical_intent_id = receipt
        self._register_order(SubmittedOrder(
            order_id=stop_id,
            role=ROLE_PROTECTIVE_STOP,
            side=action.lower(),
            quantity=fill.quantity,
            symbol=entry_order.symbol,
            stop_price=entry_order.stop_price,
            reason="stop",
            logical_intent_id=logical_intent_id,
        ))
        self._working_stop_id = stop_id
        self._events.append(Acked(order_id=stop_id))

    def _emergency_flatten(self) -> None:
        # Entry-capable configs are flatten-capable by construction (__init__),
        # so no flag check here. Best-effort: the TradovateStateError raised at
        # the call site halts the loop regardless; a cancel/liquidate failure
        # leaves the account to the operator, which is exactly what halt means.
        if self._liquidation_in_flight:
            return
        self._recovery_required = True
        self._execution_state = BrokerExecutionState.RECOVERY_REQUIRED
        self._cancel_working_orders_best_effort()
        body = {
            "accountId": self._config.account_id,
            "contractId": self._active_contract_id(),
            "admin": False,
            "isAutomated": True,
        }
        self._liquidation_in_flight = True
        try:
            receipt = self._journaled_liquidation(body)
        except TradovateError:
            return
        if receipt is None:
            return  # definitively rejected; the halt at the call site owns it
        order_id, logical_intent_id = receipt
        position = self._position
        self._register_order(SubmittedOrder(
            order_id=order_id,
            role=ROLE_EXIT,
            side="sell" if position is not None and position.side == "long" else "buy",
            quantity=position.quantity if position is not None else 0,
            symbol=self._active_contract_symbol(),
            reason="emergency_flatten",
            logical_intent_id=logical_intent_id,
        ))

    def _cancel_working_orders_best_effort(self) -> None:
        # Emergency path only: a cancel failure must not stop the liquidation.
        # Any later fill from a missed cancel is a known-id fill against an
        # impossible position state and halts through the normal guards.
        for order in list(self._orders.values()):
            if order.status != "working":
                continue
            if order.order_id in self._requested_cancel_ids:
                continue
            try:
                self._journaled_cancel(order.order_id)
                self._requested_cancel_ids.add(order.order_id)
            except TradovateError:
                continue

    def _request_cancel_or_halt(self, stop_id: str) -> None:
        if stop_id in self._requested_cancel_ids:
            # Review 2026-07-19 P0-2B: never re-POST a cancel already in
            # flight -- a duplicate would orphan the first journal intent.
            return
        try:
            self._journaled_cancel(stop_id)
        except Exception as exc:
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
            receipt = self._journaled_order_place(body, role=ROLE_EXIT)
        except TradovateStateError:
            self._pending_exit = None
            self._execution_state = BrokerExecutionState.RECOVERY_REQUIRED
            self._emergency_flatten()
            raise
        except TradovateError as exc:
            self._pending_exit = None
            self._execution_state = BrokerExecutionState.RECOVERY_REQUIRED
            raise TradovateStateError(
                "exit submission outcome unknown after stop cancellation; "
                "broker reconciliation required"
            ) from exc
        if receipt is None:
            self._pending_exit = None
            self._emergency_flatten()
            raise TradovateStateError("strategy exit response rejected")
        order_id, logical_intent_id = receipt
        self._register_order(SubmittedOrder(
            order_id=order_id,
            role=ROLE_EXIT,
            side=pending.action.lower(),
            quantity=pending.quantity,
            symbol=pending.symbol,
            reason=pending.reason,
            logical_intent_id=logical_intent_id,
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
        if not self._fill_ledger.has_open_leg:
            # Only an inherited position (P1-8 startup flatten) can close
            # without an open ledger leg: no strategy trade exists for it and
            # none is fabricated. Realized P&L re-enters through the
            # account's own records at the next stable-flat hydration.
            if self._pending_flatten is not None:
                self._resolve_pending_flatten()
            return
        trade = self._fill_ledger.close_leg(
            price=fill.price,
            timestamp_utc=fill.timestamp_utc,
            reason=order.reason or order.role,
        )
        self._strategy_feedback.append(trade)
        if self._pending_flatten is not None:
            # Either the liquidation filled, or the canceled-too-late stop
            # closed the position first (P0-2 race) -- flat either way.
            self._resolve_pending_flatten()

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
            self._emergency_flatten()
            raise TradovateStateError(
                f"protective stop {order.order_id} rejected; emergency flatten requested"
            )
        if order.role == ROLE_EXIT:
            is_flatten_liquidation = (
                order.order_id == self._pending_flatten_liquidation_id
            )
            if is_flatten_liquidation:
                # Review 2026-07-19 P0-3: a rejected flatten liquidation must
                # clear the in-flight/pending latches so a later explicit
                # retry is possible after operator review; no second
                # liquidation is attempted here.
                self._liquidation_in_flight = False
                self._pending_flatten = None
                self._pending_flatten_liquidation_id = None
            elif order.reason != "emergency_flatten":
                # Review 2026-07-19 P0-2C: ANY other exit rejection -- even
                # while a flatten awaits that exit's cancel -- leaves the
                # position without a working close and must emergency-flatten.
                self._emergency_flatten()
            self._latch_recovery()
            raise TradovateStateError(
                f"exit order {order.order_id} rejected; recovery required"
            )

    def _ingest_cancel(self, data: dict[str, Any]) -> None:
        order = self._known_order(str(data["orderId"]))
        if order.status != "working":
            # Review 2026-07-19 P0-2A: a duplicate terminal event must be
            # idempotent -- confirm any outstanding cancel intent once and
            # drop it. It must never reach the emergency branch.
            duplicate_intent = self._cancel_intent_ids.pop(order.order_id, None)
            if duplicate_intent is not None:
                self._journal().transition(duplicate_intent, IntentState.CONFIRMED)
            self._requested_cancel_ids.discard(order.order_id)
            return
        cancel_intent_id = self._cancel_intent_ids.pop(order.order_id, None)
        if cancel_intent_id is not None:
            self._journal().transition(cancel_intent_id, IntentState.CONFIRMED)
        requested = order.order_id in self._requested_cancel_ids
        self._requested_cancel_ids.discard(order.order_id)
        order.status = "canceled"
        if order.order_id == self._working_stop_id:
            self._working_stop_id = None
        self._events.append(Canceled(order_id=order.order_id))
        pending_flatten = self._pending_flatten
        if (
            pending_flatten is not None
            and order.order_id in pending_flatten.awaiting_cancel_ids
        ):
            remaining = pending_flatten.awaiting_cancel_ids - {order.order_id}
            self._pending_flatten = PendingFlatten(
                reason=pending_flatten.reason,
                awaiting_cancel_ids=remaining,
                requested_on_bar=pending_flatten.requested_on_bar,
            )
            if not remaining:
                self._submit_flatten_liquidation()
            return
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
            self._emergency_flatten()
            raise TradovateStateError(
                f"protective stop {order.order_id} canceled unexpectedly; "
                "emergency flatten requested"
            )

    def _journal(self) -> IntentJournal:
        journal = self._intent_journal
        if journal is None:
            raise TradovateStateError("broker mutation requires an intent journal")
        return journal

    def _journaled_order_place(
        self, body: dict[str, Any], *, role: str
    ) -> Optional[tuple[str, str]]:
        journal = self._journal()
        client_operation_id = _new_client_operation_id()
        wire_body = dict(body)
        wire_body["clOrdId"] = client_operation_id
        pending = journal.begin(
            role=role,
            account_id=self._config.account_id,
            contract_id=self._active_contract_id(),
            client_operation_id=client_operation_id,
            body=wire_body,
        )
        try:
            response = self._rest_client.order_place(wire_body)
        except Exception as exc:
            journal.transition(
                pending.intent_id,
                IntentState.SUBMISSION_UNKNOWN,
                detail=type(exc).__name__,
            )
            self._latch_recovery()
            raise TradovateStateError(
                f"{role.replace('_', ' ')} submission outcome unknown; "
                "reconciliation required"
            ) from exc
        return self._interpret_order_response(
            pending.intent_id,
            response,
            role=role,
        )

    def _journaled_liquidation(self, body: dict[str, Any]) -> tuple[str, str]:
        journal = self._journal()
        client_operation_id = _new_client_operation_id()
        wire_body = dict(body)
        wire_body["customTag50"] = client_operation_id
        pending = journal.begin(
            role="liquidation",
            account_id=self._config.account_id,
            contract_id=self._active_contract_id(),
            client_operation_id=client_operation_id,
            body=wire_body,
        )
        try:
            response = self._rest_client.order_liquidate_position(wire_body)
        except Exception as exc:
            journal.transition(
                pending.intent_id,
                IntentState.SUBMISSION_UNKNOWN,
                detail=type(exc).__name__,
            )
            self._latch_recovery()
            raise TradovateStateError(
                "liquidation submission outcome unknown; reconciliation required"
            ) from exc
        return self._interpret_order_response(
            pending.intent_id,
            response,
            role="liquidation",
        )

    def _journaled_cancel(self, order_id: str) -> None:
        journal = self._journal()
        client_operation_id = _new_client_operation_id()
        body = {
            "orderId": int(order_id),
            "clOrdId": client_operation_id,
            "isAutomated": True,
        }
        pending = journal.begin(
            role="cancel",
            account_id=self._config.account_id,
            contract_id=self._active_contract_id(),
            client_operation_id=client_operation_id,
            body=body,
        )
        try:
            response = self._rest_client.order_cancel(body)
        except Exception as exc:
            journal.transition(
                pending.intent_id,
                IntentState.SUBMISSION_UNKNOWN,
                detail=type(exc).__name__,
            )
            self._latch_recovery()
            raise TradovateStateError(
                f"cancel submission outcome unknown for order {order_id}; "
                "reconciliation required"
            ) from exc
        reason = _failure_reason(response)
        if reason:
            journal.transition(
                pending.intent_id,
                IntentState.REJECTED,
                detail=str(reason),
            )
            self._latch_recovery()
            raise TradovateStateError(
                f"cancel request for order {order_id} rejected: {reason}"
            )
        if not isinstance(response, dict):
            journal.transition(
                pending.intent_id,
                IntentState.SUBMISSION_UNKNOWN,
                detail="malformed_response",
            )
            self._latch_recovery()
            raise TradovateStateError(
                f"cancel response for order {order_id} is malformed"
            )
        journal.transition(pending.intent_id, IntentState.REQUEST_ACCEPTED)
        self._cancel_intent_ids[order_id] = pending.intent_id

    def _interpret_order_response(
        self,
        intent_id: str,
        response: Any,
        *,
        role: str,
    ) -> Optional[tuple[str, str]]:
        journal = self._journal()
        if isinstance(response, dict) and response.get("orderId") is not None:
            order_id = str(response["orderId"])
            journal.transition(
                intent_id,
                IntentState.ACKNOWLEDGED,
                broker_order_id=order_id,
            )
            if order_id in self._orders:
                self._latch_recovery()
                raise TradovateStateError(f"duplicate broker order id {order_id}")
            return order_id, intent_id
        reason = _failure_reason(response)
        if reason:
            journal.transition(
                intent_id,
                IntentState.REJECTED,
                detail=str(reason),
            )
            if role == ROLE_ENTRY:
                self._events.append(Rejected(order_id="", reason=str(reason)))
                return None
            self._latch_recovery()
            raise TradovateOrderRejectedError(
                f"{role} response rejected: {reason}"
            )
        journal.transition(
            intent_id,
            IntentState.SUBMISSION_UNKNOWN,
            detail="missing_order_id",
        )
        self._latch_recovery()
        raise TradovateStateError(
            f"{role} response missing orderId: {response!r}"
        )

    def _latch_recovery(self) -> None:
        self._recovery_required = True
        self._execution_state = BrokerExecutionState.RECOVERY_REQUIRED

    def _register_order(self, order: SubmittedOrder) -> None:
        if order.order_id in self._orders:
            raise TradovateStateError(f"duplicate broker order id {order.order_id}")
        self._orders[order.order_id] = order

    def _entry_is_stable_flat(self) -> bool:
        return (
            self._execution_state == BrokerExecutionState.NORMAL
            and not self._recovery_required
            and not self._daily_limit_hit
            and self._position is None
            and self._pending_exit is None
            and not self._liquidation_in_flight
            and not self._has_working_orders()
        )

    def _active_contract_symbol(self) -> str:
        symbol = self._config.contract_symbol
        if symbol is None:
            raise TradovateStateError(
                "exact contract_symbol authority is not configured"
            )
        return symbol

    def _active_contract_id(self) -> int:
        contract_id = self._config.contract_id
        if contract_id is None:
            raise TradovateStateError("exact contract_id authority is not configured")
        return contract_id

    def _require_active_symbol(self, symbol: str) -> None:
        expected = self._active_contract_symbol()
        if symbol != expected:
            raise TradovateOrderSafetyError(
                f"order contract symbol {symbol!r} does not match active contract "
                f"symbol {expected!r}"
            )

    def _require_event_identity(
        self, data: dict[str, Any], *, source: str
    ) -> None:
        for key in ("accountId", "contractId"):
            if key not in data:
                raise TradovateStateError(f"{source} is missing required {key}")
        try:
            account_id = int(data["accountId"])
            contract_id = int(data["contractId"])
        except (TypeError, ValueError) as exc:
            raise TradovateStateError(
                f"{source} contains invalid accountId or contractId"
            ) from exc
        if account_id != self._config.account_id:
            raise TradovateStateError(
                f"{source} belongs to foreign account {account_id}; "
                f"configured account is {self._config.account_id}"
            )
        active_contract_id = self._active_contract_id()
        if contract_id != active_contract_id:
            raise TradovateStateError(
                f"{source} belongs to foreign contract {contract_id}; "
                f"active contract is {active_contract_id}"
            )

    def _reconcile_position_event(self, data: dict[str, Any]) -> None:
        self._require_event_identity(data, source="broker position event")
        reported = _position_from_data(data)
        if not _positions_match(reported, self._position):
            raise TradovateStateError(
                f"broker position snapshot {reported!r} contradicts "
                f"fill-derived position {self._position!r}"
            )

    def reconcile_rest_positions(self, positions: list) -> None:
        """Cross-check a REST /position/list snapshot against fill-derived
        truth (Failure Matrix: REST vs WebSocket disagreement -> halt).

        A compliant account-scoped snapshot is empty or contains exactly one
        row for the configured active contract. Every row must carry exact
        account and contract identity. This rejects foreign accounts, stale
        roll contracts, duplicate rows, and offsetting positions instead of
        netting any of them into false agreement.
        """
        for item in positions:
            if not isinstance(item, dict):
                raise TradovateStateError(
                    f"REST position snapshot contains a non-object row {item!r}"
                )
            self._require_event_identity(item, source="REST position snapshot")
            if "netPos" not in item:
                raise TradovateStateError(
                    "REST position snapshot row is missing required netPos"
                )
        if len(positions) > 1:
            raise TradovateStateError(
                "REST position snapshot contains duplicate active-contract rows "
                f"{positions!r}; halting"
            )
        reported: Optional[BrokerPosition] = None
        if positions:
            item = positions[0]
            try:
                net = int(item["netPos"])
            except (TypeError, ValueError) as exc:
                raise TradovateStateError(
                    "REST position snapshot contains invalid netPos"
                ) from exc
        else:
            net = 0
        if net != 0:
            if positions[0].get("netPrice") is None:
                raise TradovateStateError(
                    "REST position snapshot open row is missing netPrice"
                )
            reported = BrokerPosition(
                side="long" if net > 0 else "short",
                quantity=abs(net),
                entry_price=float(positions[0]["netPrice"]),
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
    def account_realized_pnl(self) -> float:
        trade_date = self._hydrated_trade_date
        local_realized = (
            0.0
            if trade_date is None
            else self._fill_ledger.realized_session_pnl(trade_date)
        )
        return self._account_realized_pnl + local_realized

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


def _new_client_operation_id() -> str:
    return f"fp-{uuid.uuid4().hex}"


def _failure_reason(response: Any) -> Optional[str]:
    if not isinstance(response, dict):
        return None
    value = response.get("failureReason")
    if value in (None, "", "Success"):
        return None
    return str(value)
