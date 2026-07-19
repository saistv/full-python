"""The production caller for the broker's hardened order lifecycle (P1-6).

`OrderEventPump.pump()` runs inside the bar-source maintenance hook
(`live/runner.py: bars_until`), so it executes between live bars: it drains
available user-sync events, translates them (`order_events`), and feeds each
raw event to `TradovateBroker.ingest_raw_event`; on a bounded interval it
reconciles the broker's fill-derived position against an account-scoped REST
position snapshot (`reconcile_rest_positions` — the position-aware path,
valid mid-trade).

Division of authority (design §G3): the stable-flat D2 sync runtime remains
the STARTUP hydrator and flat-idle verifier; DURING a trade the broker is
authoritative and this pump feeds its lifecycle. The pump never calls
`hydrate_account_state`.

Every exception propagates: a raise surfaces through LiveLoop's existing
halt handling and the durable ``execution_halt`` ledger entry. Nothing is
swallowed here.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Optional

from full_python.tradovate.errors import TradovateStateError
from full_python.tradovate.order_events import translate_user_sync_event

_HEARTBEAT_INTERVAL_SECONDS = 2.5  # same cadence as the account runtime
_MAX_EVENTS_PER_PUMP = 512  # bound one maintenance call; the next bar re-enters


class OrderEventPump:
    def __init__(
        self,
        *,
        broker: Any,
        websocket: Any,
        rest_client: Any,
        account_id: int,
        contract_id: int,
        monotonic_clock: Callable[[], float] = time.monotonic,
        reconciliation_interval_seconds: float = 30.0,
    ) -> None:
        if (
            isinstance(reconciliation_interval_seconds, bool)
            or not isinstance(reconciliation_interval_seconds, (int, float))
            or not math.isfinite(float(reconciliation_interval_seconds))
            or float(reconciliation_interval_seconds) <= 0
        ):
            raise TradovateStateError(
                "order pump reconciliation interval must be positive and finite"
            )
        self._broker = broker
        self._websocket = websocket
        self._rest = rest_client
        self._account_id = int(account_id)
        self._contract_id = int(contract_id)
        self._clock = monotonic_clock
        self._reconciliation_interval = float(reconciliation_interval_seconds)
        now = self._clock()
        self._last_heartbeat_sent: Optional[float] = None
        self._next_reconciliation = now + self._reconciliation_interval

    def pump(self, max_wait_seconds: float = 0.0) -> int:
        """Drain available events into the broker; returns raw events delivered."""
        if (
            isinstance(max_wait_seconds, bool)
            or not isinstance(max_wait_seconds, (int, float))
            or not math.isfinite(float(max_wait_seconds))
            or max_wait_seconds < 0
        ):
            raise TradovateStateError(
                "order pump max_wait_seconds must be finite and nonnegative"
            )
        now = self._clock()
        if (
            self._last_heartbeat_sent is None
            or now - self._last_heartbeat_sent >= _HEARTBEAT_INTERVAL_SECONDS
        ):
            self._websocket.send_heartbeat()
            self._last_heartbeat_sent = now

        delivered = 0
        wait = float(max_wait_seconds)
        for _ in range(_MAX_EVENTS_PER_PUMP):
            event = self._websocket.receive_event(wait)
            wait = 0.0  # only the first receive may block
            if event is None:
                break
            if isinstance(event, dict) and event.get("e") == "shutdown":
                raise TradovateStateError(
                    f"Tradovate user-sync stream shutdown: {event.get('d')!r}"
                )
            for raw in translate_user_sync_event(
                event,
                account_id=self._account_id,
                contract_id=self._contract_id,
            ):
                self._broker.ingest_raw_event(raw)
                delivered += 1

        now = self._clock()
        if now >= self._next_reconciliation:
            positions = self._rest.position_list()
            if not isinstance(positions, list):
                raise TradovateStateError(
                    "REST position snapshot must be a list"
                )
            self._broker.reconcile_rest_positions(positions)
            self._next_reconciliation = self._clock() + self._reconciliation_interval
        return delivered
