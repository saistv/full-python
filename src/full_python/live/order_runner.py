"""Order-capable session composition root (Slice G4; audit P1-6, P3-4).

This module makes the hardened order lifecycle REACHABLE: it composes the
broker (with the shared sim/live risk veto), the OrderEventPump inside the
bar-source maintenance hook, and LiveLoop — so account events actually flow
into `ingest_raw_event` and REST reconciliation actually runs.

It is a composition skeleton, not a promotion: `main()` pins
`order_enabled=False` and `flatten_enabled=False` as literals (see
GATE 5 BOUNDARY below) and exits before any session runs. Account selection
is EXPLICIT — the operator must name the account, and it must be present in
the credential's account list. The observe runner's `accounts[0]`
auto-selection (audit P3-4) is deliberately not repeated here.
"""
from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from full_python.execution.live_loop import LiveLoop
from full_python.execution.state_machine import ExecutionInvariantError
from full_python.execution.supervisor import RiskSupervisor
from full_python.risk.limits import RiskLimits
from full_python.tradovate.broker import TradovateBroker
from full_python.tradovate.config import (
    DEMO_ENVIRONMENT,
    TradovateAdapterConfig,
)
from full_python.tradovate.errors import TradovateStateError
from full_python.tradovate.order_pump import OrderEventPump

# Production NQ limits: one contract, entries stop at the 15:59 backstop,
# RTH entries only. The broker's calendar-driven close-1 backstop still
# applies underneath on early-close sessions.
DEFAULT_RISK_LIMITS = RiskLimits(
    max_contracts=1, flatten_minutes_et=959, rth_entries_only=True
)


@dataclass(frozen=True)
class OrderSession:
    loop: LiveLoop
    pump: OrderEventPump
    broker: TradovateBroker


def require_account(
    accounts: Any, *, account_id: int, account_spec: str
) -> "dict[str, Any]":
    """Explicit account selection (P3-4): never fall back to accounts[0]."""
    if not isinstance(accounts, list) or not accounts:
        raise TradovateStateError("no Tradovate accounts visible")
    for account in accounts:
        if not isinstance(account, dict):
            continue
        if account.get("id") == account_id:
            if account.get("name") != account_spec:
                raise TradovateStateError(
                    f"account id {account_id} is named {account.get('name')!r}, "
                    f"not the configured {account_spec!r}"
                )
            return account
    raise TradovateStateError(
        f"configured account id {account_id} is not in the credential's "
        f"account list ({len(accounts)} visible); refusing to guess"
    )


def build_gate5_config(
    *,
    account_id: int,
    account_spec: str,
    contract_symbol: str,
    contract_id: int,
    root_symbol: str = "NQ",
    dollar_point_value: float,
    daily_loss_limit: float,
) -> TradovateAdapterConfig:
    """The only config `main()` will build until the Gate 5 chain passes."""
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT,
        account_spec=account_spec,
        account_id=account_id,
        root_symbol=root_symbol,
        contract_symbol=contract_symbol,
        contract_id=contract_id,
        # GATE 5 BOUNDARY: pinned literals. There is deliberately no flag,
        # argument, or environment variable that can flip these. They change
        # only by editing this source AFTER demo observe -> demo order test
        # -> paper -> reconciliation all pass (HANDOFF §6).
        order_enabled=False,
        flatten_enabled=False,
        dollar_point_value=dollar_point_value,
        daily_loss_limit=daily_loss_limit,
    )


def build_order_session(
    *,
    config: TradovateAdapterConfig,
    rest_client: Any,
    user_sync_ws: Any,
    strategy: Any,
    supervisor: RiskSupervisor,
    ledger: Any,
    bar_source_factory: Callable[[Callable[[], None]], Iterable],
    intent_journal: Any = None,
    risk_limits: Optional[RiskLimits] = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
    reconciliation_interval_seconds: float = 30.0,
) -> OrderSession:
    """Compose broker + pump-in-maintenance + LiveLoop.

    `bar_source_factory(maintenance)` receives the maintenance callable and
    must invoke it while waiting between bars (the real runner passes
    `bars_until`; tests pass a recording factory).
    """
    broker = TradovateBroker(
        config,
        rest_client,
        intent_journal=intent_journal,
        risk_limits=risk_limits
        if risk_limits is not None
        else (DEFAULT_RISK_LIMITS if config.order_enabled else None),
    )
    pump = OrderEventPump(
        broker=broker,
        websocket=user_sync_ws,
        rest_client=rest_client,
        account_id=config.account_id,
        contract_id=config.contract_id,
        monotonic_clock=monotonic_clock,
        reconciliation_interval_seconds=reconciliation_interval_seconds,
    )

    def maintenance() -> None:
        try:
            # Explicit positive wait: a zero-wait pump never reads the real
            # transport (review 2026-07-19, P0-1).
            pump.pump(max_wait_seconds=0.25)
        except ExecutionInvariantError:
            raise
        except Exception as exc:
            # Route pump/broker failures into LiveLoop's invariant-halt path:
            # durable execution_halt ledger entry, halt WITHOUT flatten
            # (position state unknown -- guardrail 5's invariant arm).
            raise ExecutionInvariantError(str(exc)) from exc

    bar_source = bar_source_factory(maintenance)
    loop = LiveLoop(bar_source, strategy, broker, supervisor, ledger)
    return OrderSession(loop=loop, pump=pump, broker=broker)


def run_startup_flatten(
    broker: TradovateBroker,
    pump: OrderEventPump,
    *,
    monotonic_clock: Callable[[], float] = time.monotonic,
    timeout_seconds: float = 30.0,
    wait_seconds: float = 0.5,
) -> None:
    """Drive an in-progress startup flatten to confirmed resolution (P1-8).

    Operator policy (2026-07-19): inherited state is flattened, never traded.
    Called after `broker.startup_flatten(...)` and BEFORE LiveLoop starts;
    pumps account events until the flatten confirms flat or the wall-clock
    deadline halts for operator review. (If LiveLoop ever started with the
    flatten unresolved, the Slice E per-bar deadline would halt on the first
    bar regardless.)
    """
    deadline = monotonic_clock() + timeout_seconds
    while broker.flatten_in_progress:
        if monotonic_clock() > deadline:
            raise TradovateStateError(
                "startup flatten unresolved within the deadline; "
                "halting for operator review"
            )
        pump.pump(max_wait_seconds=wait_seconds)
    # The recovery's order events belong to this driver, not to the trading
    # session: LiveLoop's fresh order-state shadow must not replay them (a
    # startup liquidation fill would read as a phantom short).
    broker.poll_events()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m full_python.live.order_runner",
        description=(
            "Order-capable composition root. Orders and flatten are PINNED "
            "off until the Gate 5 chain passes; today this validates "
            "credentials, explicit account selection, and composition only."
        ),
    )
    parser.add_argument("--contract-symbol", required=True)
    parser.add_argument("--contract-id", type=int, required=True)
    parser.add_argument("--point-value", type=float, required=True,
                        help="per-instrument: NQ=20.0, MNQ=2.0")
    parser.add_argument("--daily-loss-limit", type=float, default=1000.0)
    args = parser.parse_args(argv)

    account_id_raw = os.environ.get("TRADOVATE_ACCOUNT_ID")
    account_spec = os.environ.get("TRADOVATE_ACCOUNT_SPEC")
    if not account_id_raw or not account_spec:
        raise SystemExit(
            "TRADOVATE_ACCOUNT_ID and TRADOVATE_ACCOUNT_SPEC are required: "
            "account selection is explicit (audit P3-4); accounts[0] is not used"
        )

    config = build_gate5_config(
        account_id=int(account_id_raw),
        account_spec=account_spec,
        contract_symbol=args.contract_symbol,
        contract_id=args.contract_id,
        dollar_point_value=args.point_value,
        daily_loss_limit=args.daily_loss_limit,
    )
    raise SystemExit(
        "composition validated for account "
        f"{config.account_spec} (id {config.account_id}); demo order sessions "
        "are gated until Gate 5 passes -- order_enabled/flatten_enabled are "
        "pinned False in build_gate5_config()"
    )
