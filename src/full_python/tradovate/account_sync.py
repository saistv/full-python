"""Broker-authoritative startup hydration for one Tradovate account/contract."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from copy import deepcopy
import math
from typing import Any, Mapping, Optional, Sequence

from full_python.execution.broker_protocol import BrokerPosition
from full_python.tradovate.config import TradovateAdapterConfig
from full_python.tradovate.errors import TradovateStateError


REQUIRED_SYNC_COLLECTIONS = (
    "accounts",
    "contracts",
    "positions",
    "orders",
    "commands",
    "commandReports",
    "orderVersions",
    "fills",
    "cashBalances",
    "accountRiskStatuses",
)

REQUIRED_SYNC_ENTITY_TYPES = (
    "account",
    "contract",
    "position",
    "order",
    "command",
    "commandReport",
    "orderVersion",
    "fill",
    "cashBalance",
    "accountRiskStatus",
)

ENTITY_TYPE_TO_COLLECTION = dict(zip(
    REQUIRED_SYNC_ENTITY_TYPES,
    REQUIRED_SYNC_COLLECTIONS,
))

_TRANSITIONAL_ORDER_STATUSES = {
    "PendingCancel",
    "PendingNew",
    "PendingReplace",
    "Suspended",
    "Unknown",
    "Working",
}

_KNOWN_ORDER_STATUSES = _TRANSITIONAL_ORDER_STATUSES | {
    "Canceled",
    "Completed",
    "Expired",
    "Filled",
    "Rejected",
}


@dataclass(frozen=True)
class AccountHydrationSnapshot:
    account_id: int
    account_spec: str
    contract_id: int
    contract_symbol: str
    position: Optional[BrokerPosition]
    working_orders: tuple[dict[str, Any], ...]
    orders_by_id: dict[str, dict[str, Any]]
    commands_by_client_id: dict[str, dict[str, Any]]
    trade_date: str
    daily_realized_pnl: float
    entry_permitted: bool

    def __post_init__(self) -> None:
        if (
            type(self.account_id) is not int
            or type(self.contract_id) is not int
            or self.account_id <= 0
            or self.contract_id <= 0
        ):
            raise TradovateStateError(
                "hydration snapshot account and contract IDs must be positive integers"
            )
        if (
            not isinstance(self.account_spec, str)
            or not self.account_spec.strip()
            or not isinstance(self.contract_symbol, str)
            or not self.contract_symbol.strip()
        ):
            raise TradovateStateError(
                "hydration snapshot account and contract names must be nonblank"
            )
        try:
            parsed_trade_date = date.fromisoformat(self.trade_date)
        except (TypeError, ValueError) as exc:
            raise TradovateStateError(
                "hydration snapshot trade_date must be ISO YYYY-MM-DD"
            ) from exc
        if parsed_trade_date.isoformat() != self.trade_date:
            raise TradovateStateError(
                "hydration snapshot trade_date must be canonical ISO YYYY-MM-DD"
            )
        if (
            isinstance(self.daily_realized_pnl, bool)
            or not isinstance(self.daily_realized_pnl, (int, float))
            or not math.isfinite(float(self.daily_realized_pnl))
        ):
            raise TradovateStateError(
                "hydration snapshot daily_realized_pnl must be finite"
            )
        if not isinstance(self.entry_permitted, bool):
            raise TradovateStateError(
                "hydration snapshot entry_permitted must be boolean"
            )


@dataclass(frozen=True)
class AccountHydrationResult:
    snapshot: AccountHydrationSnapshot
    collections: dict[str, tuple[dict[str, Any], ...]]


class TradovateAccountHydrator:
    """Take and compare user-sync plus REST startup snapshots.

    This class intentionally does not own reconnects or incremental event
    pumping. Any caller losing the sync connection must discard this snapshot
    and run hydration again before entries are considered.
    """

    def __init__(
        self,
        config: TradovateAdapterConfig,
        *,
        user_id: int,
        expected_trade_date: date,
        websocket: Any,
        rest_client: Any,
    ) -> None:
        if user_id <= 0:
            raise TradovateStateError("user sync requires a positive user_id")
        if config.contract_id is None or config.contract_symbol is None:
            raise TradovateStateError(
                "account hydration requires exact contract identity"
            )
        if type(expected_trade_date) is not date:
            raise TradovateStateError("expected_trade_date must be a date")
        self._config = config
        self._user_id = user_id
        self._expected_trade_date = expected_trade_date.isoformat()
        self._websocket = websocket
        self._rest = rest_client

    def hydrate(self) -> AccountHydrationSnapshot:
        return self.hydrate_with_state().snapshot

    def hydrate_with_state(self) -> AccountHydrationResult:
        initial = self._websocket.request(
            "user/syncrequest",
            {
                "splitResponses": True,
                "accounts": [self._config.account_id],
                "entityTypes": list(REQUIRED_SYNC_ENTITY_TYPES),
            },
        )
        if not isinstance(initial, dict):
            raise TradovateStateError(
                "user sync initial response must be an object"
            )
        sync = {
            name: _required_entity_list(initial, name)
            for name in REQUIRED_SYNC_COLLECTIONS
        }
        detached = {
            name: tuple(deepcopy(row) for row in rows)
            for name, rows in sync.items()
        }
        snapshot = self.verify_sync_state(detached)
        return AccountHydrationResult(snapshot=snapshot, collections=detached)

    def verify_sync_state(
        self,
        collections: Mapping[str, Sequence[dict[str, Any]]],
    ) -> AccountHydrationSnapshot:
        sync = {
            name: _required_list_result(list(collections.get(name, ())), name)
            if name in collections
            else _missing_sync_collection(name)
            for name in REQUIRED_SYNC_COLLECTIONS
        }
        rest = self._fetch_rest_state()
        contract = self._rest.contract_find(self._config.contract_symbol)
        if not isinstance(contract, dict):
            raise TradovateStateError(
                f"contract {self._config.contract_symbol!r} was not found by REST"
            )
        return _normalize_and_compare(
            self._config,
            sync,
            rest,
            contract,
            expected_trade_date=self._expected_trade_date,
        )

    def _fetch_rest_state(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "accounts": _required_list_result(self._rest.account_list(), "accounts"),
            "positions": _required_list_result(self._rest.position_list(), "positions"),
            "orders": _required_list_result(self._rest.order_list(), "orders"),
            "commands": _required_list_result(self._rest.command_list(), "commands"),
            "commandReports": _required_list_result(
                self._rest.command_report_list(), "commandReports"
            ),
            "orderVersions": _required_list_result(
                self._rest.order_version_list(), "orderVersions"
            ),
            "fills": _required_list_result(self._rest.fill_list(), "fills"),
            "cashBalances": _required_list_result(
                self._rest.cash_balance_list(), "cashBalances"
            ),
            "accountRiskStatuses": _required_list_result(
                self._rest.account_risk_status_list(), "accountRiskStatuses"
            ),
        }


def _normalize_and_compare(
    config: TradovateAdapterConfig,
    sync: dict[str, list[dict[str, Any]]],
    rest: dict[str, list[dict[str, Any]]],
    rest_contract: dict[str, Any],
    *,
    expected_trade_date: str,
) -> AccountHydrationSnapshot:
    account_id = config.account_id
    contract_id = int(config.contract_id or 0)
    contract_symbol = str(config.contract_symbol)

    sync_accounts = _index(sync["accounts"], "accounts")
    rest_accounts = _index(rest["accounts"], "accounts")
    account = _exact_entity(sync_accounts, account_id, "configured account")
    rest_account = _exact_entity(rest_accounts, account_id, "REST configured account")
    _require_same(
        "accounts",
        _account_view(account),
        _account_view(rest_account),
    )
    if str(account.get("name")) != config.account_spec:
        raise TradovateStateError(
            "configured account name does not match user sync account identity"
        )
    _require_safe_account(account)

    sync_contracts = _index(sync["contracts"], "contracts")
    sync_contract = _exact_entity(sync_contracts, contract_id, "configured contract")
    _require_contract(sync_contract, contract_id, contract_symbol, "user sync")
    _require_contract(rest_contract, contract_id, contract_symbol, "REST")

    sync_positions = _account_rows(sync["positions"], account_id, "positions")
    rest_positions = _account_rows(rest["positions"], account_id, "positions")
    _require_same(
        "positions",
        _entity_views(sync_positions, _position_view, "positions"),
        _entity_views(rest_positions, _position_view, "positions"),
    )
    position = _position_authority(sync_positions, contract_id)

    sync_orders = _account_rows(sync["orders"], account_id, "orders")
    rest_orders = _account_rows(rest["orders"], account_id, "orders")
    sync_order_views = _entity_views(sync_orders, _order_view, "orders")
    rest_order_views = _entity_views(rest_orders, _order_view, "orders")
    _require_same("orders", sync_order_views, rest_order_views)
    for row in sync_orders:
        action = _required_string(row, "action", "order")
        if action not in {"Buy", "Sell"}:
            raise TradovateStateError(f"unknown order action {action!r}")
        status = _required_string(row, "ordStatus", "order")
        if status not in _KNOWN_ORDER_STATUSES:
            raise TradovateStateError(f"unknown order status {status!r}")
    working_orders = tuple(
        row for row in sync_orders
        if _required_string(row, "ordStatus", "order") in _TRANSITIONAL_ORDER_STATUSES
    )
    for row in working_orders:
        row_contract = _required_int(row, "contractId", "order")
        if row_contract != contract_id:
            raise TradovateStateError(
                f"foreign-contract working order {row.get('id')!r} exists in "
                f"configured account for contract {row_contract}"
            )

    order_contracts = {
        _required_int(row, "id", "order"): _required_int(
            row, "contractId", "order"
        )
        for row in sync_orders
    }
    order_ids = set(order_contracts)
    _compare_order_dependents(sync, rest, order_contracts, contract_id)

    sync_cash = _account_rows(sync["cashBalances"], account_id, "cashBalances")
    rest_cash = _account_rows(rest["cashBalances"], account_id, "cashBalances")
    _require_same(
        "cashBalances",
        _entity_views(sync_cash, _cash_view, "cashBalances"),
        _entity_views(rest_cash, _cash_view, "cashBalances"),
    )
    if len(sync_cash) != 1:
        raise TradovateStateError(
            "configured account must have exactly one unambiguous cash balance"
        )
    trade_date = _trade_date(sync_cash[0].get("tradeDate"), "cash balance")
    if trade_date != expected_trade_date:
        raise TradovateStateError(
            f"cash balance trade date {trade_date} does not match expected "
            f"session {expected_trade_date}"
        )
    daily_realized_pnl = _finite_float(
        sync_cash[0].get("realizedPnL"), "cash balance realizedPnL"
    )

    sync_risk = _risk_rows(sync["accountRiskStatuses"], account_id)
    rest_risk = _risk_rows(rest["accountRiskStatuses"], account_id)
    _require_same(
        "accountRiskStatuses",
        _entity_views(sync_risk, _risk_view, "accountRiskStatuses"),
        _entity_views(rest_risk, _risk_view, "accountRiskStatuses"),
    )
    if len(sync_risk) != 1:
        raise TradovateStateError(
            "configured account must have exactly one account risk status"
        )
    _require_normal_risk(sync_risk[0])

    orders_by_id = {
        str(_required_int(row, "id", "order")): dict(row) for row in sync_orders
    }
    commands_by_client_id = _commands_by_client_id(
        sync["commands"], order_ids
    )
    return AccountHydrationSnapshot(
        account_id=account_id,
        account_spec=config.account_spec,
        contract_id=contract_id,
        contract_symbol=contract_symbol,
        position=position,
        working_orders=tuple(dict(row) for row in working_orders),
        orders_by_id=orders_by_id,
        commands_by_client_id=commands_by_client_id,
        trade_date=trade_date,
        daily_realized_pnl=daily_realized_pnl,
        entry_permitted=position is None and not working_orders,
    )


def _compare_order_dependents(
    sync, rest, order_contracts: dict[int, int], contract_id: int
) -> None:
    order_ids = set(order_contracts)
    sync_commands = _dependent_rows(sync["commands"], order_ids, "commands")
    rest_commands = _dependent_rows(rest["commands"], order_ids, "commands")
    _require_same(
        "commands",
        _entity_views(sync_commands, _command_view, "commands"),
        _entity_views(rest_commands, _command_view, "commands"),
    )
    command_ids = {
        _required_int(row, "id", "command") for row in sync_commands
    }
    sync_reports = _command_report_rows(sync["commandReports"], command_ids)
    rest_reports = _command_report_rows(rest["commandReports"], command_ids)
    _require_same(
        "commandReports",
        _entity_views(sync_reports, _command_report_view, "commandReports"),
        _entity_views(rest_reports, _command_report_view, "commandReports"),
    )
    sync_versions = _dependent_rows(
        sync["orderVersions"], order_ids, "orderVersions"
    )
    rest_versions = _dependent_rows(
        rest["orderVersions"], order_ids, "orderVersions"
    )
    _require_same(
        "orderVersions",
        _entity_views(sync_versions, _order_version_view, "orderVersions"),
        _entity_views(rest_versions, _order_version_view, "orderVersions"),
    )

    sync_fills = _fills_for_account(
        sync["fills"], order_contracts, contract_id
    )
    rest_fills = _fills_for_account(
        rest["fills"], order_contracts, contract_id
    )
    _require_same(
        "fills",
        _entity_views(sync_fills, _fill_view, "fills"),
        _entity_views(rest_fills, _fill_view, "fills"),
    )


def _required_entity_list(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    if name not in payload:
        raise TradovateStateError(
            f"user sync initial response is missing required {name} collection"
        )
    return _required_list_result(payload[name], name)


def _missing_sync_collection(name: str):
    raise TradovateStateError(
        f"user sync initial response is missing required {name} collection"
    )


def _required_list_result(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TradovateStateError(f"{name} snapshot must be a list")
    for row in value:
        if not isinstance(row, dict):
            raise TradovateStateError(f"{name} contains a non-object entity")
    return value


def _index(rows: list[dict[str, Any]], name: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        entity_id = _required_int(row, "id", name)
        if entity_id in result:
            raise TradovateStateError(f"duplicate {name} entity id {entity_id}")
        result[entity_id] = row
    return result


def _exact_entity(index, entity_id: int, label: str) -> dict[str, Any]:
    try:
        return index[entity_id]
    except KeyError as exc:
        raise TradovateStateError(f"{label} {entity_id} is missing") from exc


def _account_rows(rows, account_id: int, name: str):
    _index(rows, name)
    result = []
    for row in rows:
        if _required_int(row, "accountId", name) == account_id:
            result.append(row)
    return result


def _risk_rows(rows, account_id: int):
    indexed = _index(rows, "accountRiskStatuses")
    row = indexed.get(account_id)
    return [] if row is None else [row]


def _dependent_rows(rows, order_ids: set[int], name: str):
    _index(rows, name)
    return [
        row for row in rows
        if _required_int(row, "orderId", name) in order_ids
    ]


def _command_report_rows(rows, command_ids: set[int]):
    _index(rows, "commandReports")
    return [
        row for row in rows
        if _required_int(row, "commandId", "commandReport") in command_ids
    ]


def _fills_for_account(rows, order_contracts: dict[int, int], contract_id: int):
    _index(rows, "fills")
    relevant = []
    for row in rows:
        fill_contract = _required_int(row, "contractId", "fill")
        order_id = _required_int(row, "orderId", "fill")
        order_contract = order_contracts.get(order_id)
        if fill_contract == contract_id and order_contract is None:
            raise TradovateStateError(
                f"fill {row.get('id')!r} for active contract cannot be joined "
                "to an order in the configured account"
            )
        if order_contract is not None and fill_contract != order_contract:
            raise TradovateStateError(
                f"fill {row.get('id')!r} contract {fill_contract} does not "
                f"match order {order_id} contract {order_contract}"
            )
        if order_contract is not None:
            relevant.append(row)
    return relevant


def _entity_views(rows, view, name):
    result = {}
    for row in rows:
        entity_id = _required_int(row, "id", name)
        if entity_id in result:
            raise TradovateStateError(f"duplicate {name} entity id {entity_id}")
        result[entity_id] = view(row)
    return result


def _require_same(name: str, sync_value: Any, rest_value: Any) -> None:
    if sync_value != rest_value:
        raise TradovateStateError(
            f"user sync and REST {name} disagree: "
            f"sync={sync_value!r}, rest={rest_value!r}"
        )


def _account_view(row):
    return {
        "id": _required_int(row, "id", "account"),
        "name": _required_string(row, "name", "account"),
        "closed": _optional_bool(row, "closed", "account", default=False),
        "readonly": _optional_bool(row, "readonly", "account", default=False),
        "futuresDisabled": _optional_bool(
            row, "futuresDisabled", "account", default=False
        ),
    }


def _position_view(row):
    return {
        "id": _required_int(row, "id", "position"),
        "accountId": _required_int(row, "accountId", "position"),
        "contractId": _required_int(row, "contractId", "position"),
        "netPos": _required_int(row, "netPos", "position"),
        "netPrice": row.get("netPrice"),
    }


def _order_view(row):
    return {
        "id": _required_int(row, "id", "order"),
        "accountId": _required_int(row, "accountId", "order"),
        "contractId": _required_int(row, "contractId", "order"),
        "action": _required_string(row, "action", "order"),
        "ordStatus": _required_string(row, "ordStatus", "order"),
        "admin": _optional_bool(row, "admin", "order", default=False),
    }


def _command_view(row):
    return {
        "id": _required_int(row, "id", "command"),
        "orderId": _required_int(row, "orderId", "command"),
        "commandType": _required_string(row, "commandType", "command"),
        "commandStatus": _required_string(row, "commandStatus", "command"),
        "clOrdId": row.get("clOrdId"),
        "customTag50": row.get("customTag50"),
        "isAutomated": _optional_bool(
            row, "isAutomated", "command", default=False
        ),
    }


def _command_report_view(row):
    return {
        "id": _required_int(row, "id", "commandReport"),
        "commandId": _required_int(row, "commandId", "commandReport"),
        "commandStatus": row.get("commandStatus"),
        "ordStatus": row.get("ordStatus"),
        "rejectReason": row.get("rejectReason"),
    }


def _order_version_view(row):
    return {
        "id": _required_int(row, "id", "orderVersion"),
        "orderId": _required_int(row, "orderId", "orderVersion"),
        "orderQty": _required_int(row, "orderQty", "orderVersion"),
        "orderType": _required_string(row, "orderType", "orderVersion"),
        "stopPrice": row.get("stopPrice"),
        "price": row.get("price"),
    }


def _fill_view(row):
    return {
        "id": _required_int(row, "id", "fill"),
        "orderId": _required_int(row, "orderId", "fill"),
        "contractId": _required_int(row, "contractId", "fill"),
        "action": _required_string(row, "action", "fill"),
        "qty": _required_int(row, "qty", "fill"),
        "price": _finite_float(row.get("price"), "fill price"),
        "active": _optional_bool(row, "active", "fill", default=False),
    }


def _cash_view(row):
    return {
        "id": _required_int(row, "id", "cashBalance"),
        "accountId": _required_int(row, "accountId", "cashBalance"),
        "tradeDate": _trade_date(row.get("tradeDate"), "cash balance"),
        "realizedPnL": _finite_float(
            row.get("realizedPnL"), "cash balance realizedPnL"
        ),
    }


def _risk_view(row):
    return {
        "id": _required_int(row, "id", "accountRiskStatus"),
        "adminAction": row.get("adminAction"),
        "liquidateOnly": _optional_bool(
            row, "liquidateOnly", "accountRiskStatus", allow_none=True
        ),
        "userTriggeredLiqOnly": _optional_bool(
            row, "userTriggeredLiqOnly", "accountRiskStatus", default=False
        ),
    }


def _position_authority(rows, active_contract_id: int) -> Optional[BrokerPosition]:
    active = None
    for row in rows:
        net = _required_int(row, "netPos", "position")
        contract_id = _required_int(row, "contractId", "position")
        if net == 0:
            continue
        if contract_id != active_contract_id:
            raise TradovateStateError(
                f"unexpected nonzero position in configured account for foreign "
                f"contract {contract_id}"
            )
        if active is not None:
            raise TradovateStateError(
                "duplicate nonzero active-contract positions in configured account"
            )
        price = _finite_float(row.get("netPrice"), "position netPrice")
        active = BrokerPosition(
            side="long" if net > 0 else "short",
            quantity=abs(net),
            entry_price=price,
        )
    return active


def _commands_by_client_id(rows, order_ids: set[int]):
    result = {}
    for row in rows:
        order_id = _required_int(row, "orderId", "command")
        if order_id not in order_ids:
            continue
        for field in ("clOrdId", "customTag50"):
            client_id = row.get(field)
            if client_id is None:
                continue
            if not isinstance(client_id, str) or not client_id:
                raise TradovateStateError(
                    f"command {field} must be a nonblank string"
                )
            if len(client_id) > 64:
                raise TradovateStateError(
                    f"command {field} must be no longer than 64 characters"
                )
            if client_id in result:
                raise TradovateStateError(
                    f"duplicate command client operation id {client_id!r}"
                )
            result[client_id] = dict(row)
    return result


def _require_safe_account(account: dict[str, Any]) -> None:
    if account.get("closed") is True:
        raise TradovateStateError("configured account is closed")
    if account.get("readonly") is True:
        raise TradovateStateError("configured account is read-only")
    if account.get("futuresDisabled") is True:
        raise TradovateStateError("configured account is futures-disabled")


def _require_normal_risk(risk: dict[str, Any]) -> None:
    if risk.get("adminAction") not in (None, "Normal"):
        raise TradovateStateError(
            f"account risk status is not normal: {risk.get('adminAction')!r}"
        )
    if risk.get("liquidateOnly") not in (None, False):
        raise TradovateStateError("account risk status is liquidation-only")
    if risk.get("userTriggeredLiqOnly") is True:
        raise TradovateStateError("account risk status is liquidation-only")


def _require_contract(row, contract_id: int, symbol: str, source: str) -> None:
    if _required_int(row, "id", f"{source} contract") != contract_id:
        raise TradovateStateError(f"{source} contract ID does not match configuration")
    if _required_string(row, "name", f"{source} contract") != symbol:
        raise TradovateStateError(f"{source} contract symbol does not match configuration")


def _required_int(row: dict[str, Any], key: str, source: str) -> int:
    if key not in row:
        raise TradovateStateError(f"{source} is missing required {key}")
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TradovateStateError(f"{source} has invalid {key}")
    return value


def _required_string(row: dict[str, Any], key: str, source: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TradovateStateError(f"{source} is missing required {key}")
    return value


def _finite_float(value: Any, source: str) -> float:
    if isinstance(value, bool):
        raise TradovateStateError(f"{source} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TradovateStateError(f"{source} must be numeric") from exc
    if not math.isfinite(result):
        raise TradovateStateError(f"{source} must be finite")
    return result


def _optional_bool(
    row: dict[str, Any],
    key: str,
    source: str,
    *,
    default: Optional[bool] = None,
    allow_none: bool = False,
) -> Optional[bool]:
    value = row.get(key, default)
    if value is None and allow_none:
        return None
    if not isinstance(value, bool):
        raise TradovateStateError(f"{source} {key} must be boolean")
    return value


def _trade_date(value: Any, source: str) -> str:
    if not isinstance(value, dict):
        raise TradovateStateError(f"{source} is missing required tradeDate")
    try:
        parts = []
        for key in ("year", "month", "day"):
            part = value[key]
            if isinstance(part, bool) or not isinstance(part, int):
                raise ValueError(key)
            parts.append(part)
        parsed = date(
            parts[0],
            parts[1],
            parts[2],
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise TradovateStateError(f"{source} has invalid tradeDate") from exc
    return parsed.isoformat()
