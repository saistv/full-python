# Tradovate Broker Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six tracked risk-management gaps in `TradovateBroker` and complete the Broker Failure Test Matrix (12/28 → 28/28), entirely offline.

**Architecture:** A new pure `FillPairingLedger` pairs real broker fills into `models.Trade` objects (broker-truth accounting); `TradovateBroker` gains a submitted-order map, broker-held frozen protective stops, a cancel-then-close exit path, and fill-derived session P&L / DLL. `LiveLoop`, `PositionEngine`, `OrderStateMachine`, `RiskSupervisor`, and all strategy code are untouched.

**Tech Stack:** Python 3.9-compatible stdlib only. pytest. Fake REST/WS transports (already in the test suite).

**Spec:** `docs/superpowers/specs/2026-07-10-tradovate-gap-closure-design.md` (and its parent, `2026-07-07-tradovate-adapter-design.md`).

## Global Constraints

- Branch: `claude/m4-regime`. Repo: `/Users/sais/Documents/New Beginning/full-python`. Never commit red tests.
- Python 3.9 compatible: `from __future__ import annotations` at top of every module; no `X | Y` outside annotations; no `Z`-suffix `fromisoformat`.
- Stdlib only. No new dependencies.
- No changes to `LiveLoop`, `PositionEngine`, `OrderStateMachine`, `RiskSupervisor`, strategy code, or production config values.
- `order_enabled` / `flatten_enabled` stay `False` by default. Nothing connects to a real endpoint.
- `TradovateStateError` must halt `LiveLoop` — it subclasses `ExecutionInvariantError` (Task 1) so the existing halt path catches it. Invariant halts do NOT flatten (position truth unknown).
- Stops are frozen at entry (production fill policy). The broker-held protective stop is never modified after placement; `result.stop_updates` are deliberately not applied.
- Run `python3 -m pytest -q` at the end of every task. Baseline before Task 1: 254 passed, 3 skipped.

## Failure Matrix Audit (28 rows)

Verified against the suite at plan time (`git rev-parse HEAD` = b88da16). "NEW-Tn" = added by Task n of this plan.

| # | Row | Status / covering test |
|---|---|---|
| 1 | auth success | ✅ `test_auth_client_requests_access_token_with_credentials_payload` |
| 2 | auth failure | ✅ `test_missing_token_fields_raise_auth_error_naming_missing_field`, `test_invalid_token_values_raise_auth_error_without_coercion` |
| 3 | token renewal before expiry | ✅ `test_auth_client_renews_access_token_with_old_token_authorization`, `test_token_should_renew_when_remaining_lifetime_is_within_lead_seconds` |
| 4 | rate-limit/time-penalty | ✅ `test_http_client_raises_rate_limit_error_with_retry_details` |
| 5 | WS authorization success/failure | ✅ success: `test_authorize_sends_token_frame_and_accepts_success_response`; failure: NEW-T7 `test_ws_authorize_failure_status_raises` |
| 6 | WS disconnect before order acknowledgement | NEW-T7 `test_ws_close_frame_while_waiting_for_response_raises` |
| 7 | chart subscription success | ✅ `test_subscribe_requests_minute_chart_and_stores_subscription_ids` |
| 8 | malformed chart data | NEW-T7 `test_malformed_chart_bar_raises_value_error` |
| 9 | duplicate chart bar timestamp | ✅ `test_next_bar_queues_unique_matching_chart_bars_before_reading_more_events` |
| 10 | chart subscription cancel | ✅ `test_cancel_requests_cancel_chart_when_realtime_id_is_known`, `test_cancel_without_realtime_id_does_not_request_cancel_chart` |
| 11 | order placement disabled | ✅ `test_orders_disabled_rejects_order_intent_without_calling_rest` |
| 12 | market order placement success | ✅ `test_orders_enabled_places_automated_market_order_and_emits_ack` (updated T3) |
| 13 | live order without stop_price rejected | ✅ `test_live_enabled_entry_requires_stop_price_metadata` (updated T3) |
| 14 | protective stop submitted after entry fill | NEW-T4 `test_entry_fill_submits_protective_stop_at_frozen_price` |
| 15 | stop+target OCO after entry fill | **N/A-BY-DESIGN** — production strategy never emits `target_price`; recorded in spec + parent amendment (T9). No OCO code. |
| 16 | protective-order confirmation failure → fatal | NEW-T4 `test_protective_stop_rest_failure_flattens_and_raises`, `test_protective_stop_rejection_flattens_and_raises` |
| 17 | order rejection | NEW-T4 `test_reject_event_for_known_entry_emits_rejected` |
| 18 | order cancellation | NEW-T5 `test_cancel_event_for_known_order_emits_canceled` |
| 19 | duplicate fill same order id | NEW-T3 `test_duplicate_fill_for_same_order_id_raises_state_error` (adapter level; `OrderStateMachine` covers it independently) |
| 20 | fill for unknown order id | NEW-T3 `test_fill_for_unknown_order_id_raises_state_error` |
| 21 | partial fill event | ✅ `test_partial_fill_raw_event_maps_to_partial_filled`, `test_partial_fill_event_from_broker_is_fatal_for_order_state_machine` (both updated T3) |
| 22 | broker position exists, state machine flat | NEW-T7 `test_position_snapshot_with_position_while_fill_derived_flat_raises` + NEW-T8 LiveLoop halt test |
| 23 | state machine position, broker flat | NEW-T7 `test_flat_position_snapshot_while_fill_derived_open_raises` |
| 24 | flatten disabled by config | ✅ `test_flatten_disabled_raises_and_does_not_call_liquidation` (updated T5) |
| 25 | flatten requested while flat | NEW-T5 `test_flatten_while_flat_is_a_no_op` |
| 26 | flatten requested while long | ✅ `test_flatten_enabled_with_position_calls_liquidate_position` (updated T5) |
| 27 | flatten requested while short | NEW-T5 `test_flatten_while_short_cancels_stop_then_liquidates` |
| 28 | REST position snapshot disagreement with WS state | NEW-T7 `test_rest_position_snapshot_disagreement_raises` |

---

### Task 1: `TradovateStateError` + adapter risk/cost config fields

**Files:**
- Modify: `src/full_python/tradovate/errors.py`
- Modify: `src/full_python/tradovate/config.py`
- Test: `tests/test_tradovate_config.py`

**Interfaces:**
- Produces: `TradovateStateError(TradovateError, ExecutionInvariantError)`; `TradovateAdapterConfig.dollar_point_value: Optional[float] = None`, `.commission_per_contract_round_trip: float = 0.0`, `.daily_loss_limit: Optional[float] = None` with positivity validation. Flags stay independent at config level (pinned by `test_live_order_and_flatten_flags_are_independent` — do NOT couple them here; the broker couples them in Task 3).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tradovate_config.py`:

```python
def test_state_error_is_an_execution_invariant() -> None:
    from full_python.execution.state_machine import ExecutionInvariantError
    from full_python.tradovate.errors import TradovateError, TradovateStateError

    assert issubclass(TradovateStateError, TradovateError)
    assert issubclass(TradovateStateError, ExecutionInvariantError)


def test_adapter_config_risk_fields_default_unset() -> None:
    cfg = TradovateAdapterConfig(environment=DEMO_ENVIRONMENT, account_spec="SIM123", account_id=456)
    assert cfg.dollar_point_value is None
    assert cfg.commission_per_contract_round_trip == 0.0
    assert cfg.daily_loss_limit is None


def test_adapter_config_rejects_non_positive_risk_values() -> None:
    from full_python.tradovate.errors import TradovateConfigError

    with pytest.raises(TradovateConfigError, match="dollar_point_value"):
        TradovateAdapterConfig(
            environment=DEMO_ENVIRONMENT, account_spec="S", account_id=1, dollar_point_value=0.0
        )
    with pytest.raises(TradovateConfigError, match="commission"):
        TradovateAdapterConfig(
            environment=DEMO_ENVIRONMENT, account_spec="S", account_id=1,
            commission_per_contract_round_trip=-0.01,
        )
    with pytest.raises(TradovateConfigError, match="daily_loss_limit"):
        TradovateAdapterConfig(
            environment=DEMO_ENVIRONMENT, account_spec="S", account_id=1, daily_loss_limit=0.0
        )
```

(`pytest` is already imported in this file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tradovate_config.py -q`
Expected: 3 failures — `ImportError: cannot import name 'TradovateStateError'` and `TypeError: unexpected keyword argument 'dollar_point_value'`.

- [ ] **Step 3: Implement**

In `src/full_python/tradovate/errors.py`, add after the imports (below `from typing import Optional`):

```python
from full_python.execution.state_machine import ExecutionInvariantError
```

and add at the end of the file:

```python
class TradovateStateError(TradovateError, ExecutionInvariantError):
    """Broker/account state can no longer be proven.

    Subclasses ExecutionInvariantError so LiveLoop's existing
    invariant-halt path catches it: halt WITHOUT flatten (position truth
    unknown). Never catch-and-continue this in adapter code.
    """
```

In `src/full_python/tradovate/config.py`, replace the `TradovateAdapterConfig` dataclass with:

```python
@dataclass(frozen=True)
class TradovateAdapterConfig:
    environment: TradovateEnvironment
    account_spec: str
    account_id: int
    root_symbol: str = "NQ"
    order_enabled: bool = False
    flatten_enabled: bool = False
    token_renewal_lead_seconds: int = 15 * 60
    # Risk/cost model, mirroring SimulationConfig semantics. PER-INSTRUMENT:
    # NQ = 20.0 $/pt, MNQ = 2.0 $/pt -- no default, so a value can never
    # silently cross instruments. TradovateBroker refuses to construct
    # without dollar_point_value (and, when order_enabled, without
    # daily_loss_limit + flatten_enabled).
    dollar_point_value: Optional[float] = None
    commission_per_contract_round_trip: float = 0.0
    daily_loss_limit: Optional[float] = None

    def __post_init__(self) -> None:
        if self.dollar_point_value is not None and self.dollar_point_value <= 0:
            raise TradovateConfigError("dollar_point_value must be positive when set")
        if self.commission_per_contract_round_trip < 0:
            raise TradovateConfigError("commission_per_contract_round_trip must be >= 0")
        if self.daily_loss_limit is not None and self.daily_loss_limit <= 0:
            raise TradovateConfigError("daily_loss_limit must be positive when set")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tradovate_config.py -q`
Expected: all pass (existing `test_live_order_and_flatten_flags_are_independent` must still pass — config does not couple the flags).

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q` — expected 257 passed, 3 skipped.

```bash
git add src/full_python/tradovate/errors.py src/full_python/tradovate/config.py tests/test_tradovate_config.py
git commit -m "feat: add TradovateStateError and adapter risk/cost config fields"
```

---

### Task 2: `FillPairingLedger`

**Files:**
- Create: `src/full_python/tradovate/ledger.py`
- Test: `tests/test_tradovate_ledger.py`

**Interfaces:**
- Consumes: `models.Trade`, `TradovateStateError` (Task 1).
- Produces (used by Task 3+): `FillPairingLedger(dollar_point_value: float, commission_per_contract_round_trip: float)` with `open_leg(*, symbol, side, quantity, price, timestamp_utc, stop_price, session_date) -> None`, `mark_bar(*, high, low) -> None`, `close_leg(*, price, timestamp_utc, reason) -> Trade`, `realized_session_pnl(session_date: str) -> float`, properties `trades: list[Trade]`, `has_open_leg: bool`. `side` argument is fill-side `"buy"|"sell"`; `session_date` is an isoformat string (entry session — matches sim, which stamps `session_date` at entry).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tradovate_ledger.py`:

```python
from __future__ import annotations

import pytest

from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger
from full_python.models import ExitDecision, MarketBar, OrderIntent, StrategyResult
from full_python.simulation.config import SimulationConfig
from full_python.simulation.position_engine import PositionEngine
from full_python.tradovate.errors import TradovateStateError
from full_python.tradovate.ledger import FillPairingLedger


def _ledger(commission: float = 1.0) -> FillPairingLedger:
    return FillPairingLedger(dollar_point_value=20.0, commission_per_contract_round_trip=commission)


def test_pairs_entry_and_exit_fills_into_a_trade() -> None:
    ledger = _ledger()
    ledger.open_leg(
        symbol="NQ", side="buy", quantity=1, price=100.0,
        timestamp_utc="2026-07-07T14:32:00Z", stop_price=95.0, session_date="2026-07-07",
    )
    assert ledger.has_open_leg

    trade = ledger.close_leg(price=110.0, timestamp_utc="2026-07-07T14:40:00Z", reason="atf_flip")

    assert not ledger.has_open_leg
    assert trade.side == "long"
    assert trade.gross_points == 10.0
    assert trade.gross_pnl == 200.0
    assert trade.commission == 1.0
    assert trade.net_pnl == 199.0
    assert trade.stop_price == 95.0
    assert trade.exit_reason == "atf_flip"
    assert trade.session_date == "2026-07-07"
    assert ledger.trades == [trade]


def test_short_leg_signs_and_excursions() -> None:
    ledger = _ledger()
    ledger.open_leg(
        symbol="NQ", side="sell", quantity=2, price=100.0,
        timestamp_utc="2026-07-07T14:32:00Z", stop_price=105.0, session_date="2026-07-07",
    )
    ledger.mark_bar(high=101.0, low=97.0)
    ledger.mark_bar(high=99.0, low=96.0)

    trade = ledger.close_leg(price=98.0, timestamp_utc="2026-07-07T14:45:00Z", reason="stop")

    assert trade.side == "short"
    assert trade.quantity == 2
    assert trade.gross_points == 2.0
    assert trade.gross_pnl == 80.0        # 2pt * $20 * 2 contracts
    assert trade.net_pnl == 78.0
    assert trade.mfe_points == 4.0        # entry 100 -> low 96
    assert trade.mae_points == 1.0        # entry 100 -> high 101


def test_realized_session_pnl_accumulates_per_session() -> None:
    ledger = _ledger()
    ledger.open_leg(symbol="NQ", side="buy", quantity=1, price=100.0,
                    timestamp_utc="2026-07-07T14:32:00Z", stop_price=95.0, session_date="2026-07-07")
    ledger.close_leg(price=95.0, timestamp_utc="2026-07-07T14:35:00Z", reason="stop")
    ledger.open_leg(symbol="NQ", side="buy", quantity=1, price=94.0,
                    timestamp_utc="2026-07-07T14:50:00Z", stop_price=90.0, session_date="2026-07-07")
    ledger.close_leg(price=90.0, timestamp_utc="2026-07-07T14:55:00Z", reason="stop")

    assert ledger.realized_session_pnl("2026-07-07") == pytest.approx(-182.0)  # (-100-1) + (-80-1)
    assert ledger.realized_session_pnl("2026-07-08") == 0.0


def test_double_open_and_orphan_close_raise() -> None:
    ledger = _ledger()
    with pytest.raises(TradovateStateError, match="no open leg"):
        ledger.close_leg(price=100.0, timestamp_utc="2026-07-07T14:32:00Z", reason="stop")

    ledger.open_leg(symbol="NQ", side="buy", quantity=1, price=100.0,
                    timestamp_utc="2026-07-07T14:32:00Z", stop_price=95.0, session_date="2026-07-07")
    with pytest.raises(TradovateStateError, match="already open"):
        ledger.open_leg(symbol="NQ", side="buy", quantity=1, price=101.0,
                        timestamp_utc="2026-07-07T14:33:00Z", stop_price=96.0, session_date="2026-07-07")


def test_trade_matches_position_engine_for_identical_fills() -> None:
    """Parity pin: identical fills through the sim and the ledger produce
    the identical Trade (zero-slippage sim config so fill prices match)."""
    config = SimulationConfig(
        point_value=20.0,
        commission_per_contract_round_trip=1.0,
        entry_slippage_points=0.0,
        exit_slippage_points=0.0,
        rth_open_extra_entry_slippage_points=0.0,
        rth_entries_only=False,
    )
    engine = PositionEngine(config, object(), EventLedger())

    def bar(ts: str, o: float, h: float, lo: float, c: float) -> MarketBar:
        return MarketBar(timestamp_utc=ts, symbol="NQ", open=o, high=h, low=lo, close=c, volume=1.0)

    bar1 = bar("2026-07-07T14:31:00Z", 100.0, 100.5, 99.5, 100.0)
    bar2 = bar("2026-07-07T14:32:00Z", 101.0, 103.0, 100.5, 102.0)
    bar3 = bar("2026-07-07T14:33:00Z", 102.5, 102.5, 102.5, 102.5)
    s1, s2, s3 = (classify_timestamp(b.timestamp_utc) for b in (bar1, bar2, bar3))

    engine.process_pre_strategy(bar1, s1)
    engine.apply_strategy_result(bar1, s1, StrategyResult(order_intents=(
        OrderIntent.market_entry(
            timestamp_utc=bar1.timestamp_utc, symbol="NQ", side="buy", quantity=1,
            reason="adaptive_trend", metadata={"stop_price": 95.0},
        ),
    )))
    engine.note_bar_processed(bar1, s1)
    engine.process_pre_strategy(bar2, s2)            # entry fills at bar2.open = 101.0
    engine.apply_strategy_result(bar2, s2, StrategyResult(exits=(
        ExitDecision(timestamp_utc=bar2.timestamp_utc, symbol="NQ", reason="atf_flip"),
    )))
    engine.note_bar_processed(bar2, s2)
    engine.process_pre_strategy(bar3, s3)            # exit fills at bar3.open = 102.5
    sim_trade = engine.trades[0]

    ledger = _ledger()
    ledger.open_leg(symbol="NQ", side="buy", quantity=1, price=101.0,
                    timestamp_utc=bar2.timestamp_utc, stop_price=95.0,
                    session_date=s2.session_date.isoformat())
    ledger.mark_bar(high=bar2.high, low=bar2.low)    # sim counts the entry bar's range
    trade = ledger.close_leg(price=102.5, timestamp_utc=bar3.timestamp_utc, reason="atf_flip")

    assert trade == sim_trade
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tradovate_ledger.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'full_python.tradovate.ledger'`.

- [ ] **Step 3: Implement `src/full_python/tradovate/ledger.py`**

```python
"""Fill-derived trade bookkeeping for a real broker adapter.

Pure, no I/O. Pairs REAL broker fills into models.Trade so that session
realized P&L, RiskSupervisor's trades view, and the strategy's DLL all
run on broker truth instead of simulated fills. The arithmetic mirrors
PositionEngine._close_position exactly (pinned by
tests/test_tradovate_ledger.py::test_trade_matches_position_engine_for_identical_fills);
only the fill source differs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from full_python.models import Trade
from full_python.tradovate.errors import TradovateStateError


@dataclass
class _OpenLeg:
    symbol: str
    side: str  # "long" | "short"
    quantity: int
    entry_timestamp_utc: str
    entry_price: float
    stop_price: float
    session_date: str
    mfe_points: float = 0.0
    mae_points: float = 0.0


class FillPairingLedger:
    def __init__(
        self, *, dollar_point_value: float, commission_per_contract_round_trip: float
    ) -> None:
        if dollar_point_value <= 0:
            raise TradovateStateError("dollar_point_value must be positive")
        if commission_per_contract_round_trip < 0:
            raise TradovateStateError("commission_per_contract_round_trip must be >= 0")
        self._dollar_point_value = dollar_point_value
        self._commission_round_trip = commission_per_contract_round_trip
        self._open_leg: Optional[_OpenLeg] = None
        self._trades: list[Trade] = []

    @property
    def has_open_leg(self) -> bool:
        return self._open_leg is not None

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    def open_leg(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        timestamp_utc: str,
        stop_price: float,
        session_date: str,
    ) -> None:
        if self._open_leg is not None:
            raise TradovateStateError("entry fill while a leg is already open")
        if side not in ("buy", "sell"):
            raise TradovateStateError(f"unsupported fill side: {side}")
        self._open_leg = _OpenLeg(
            symbol=symbol,
            side="long" if side == "buy" else "short",
            quantity=quantity,
            entry_timestamp_utc=timestamp_utc,
            entry_price=price,
            stop_price=stop_price,
            session_date=session_date,
        )

    def mark_bar(self, *, high: float, low: float) -> None:
        leg = self._open_leg
        if leg is None:
            return
        if leg.side == "long":
            leg.mfe_points = max(leg.mfe_points, high - leg.entry_price)
            leg.mae_points = max(leg.mae_points, leg.entry_price - low)
        else:
            leg.mfe_points = max(leg.mfe_points, leg.entry_price - low)
            leg.mae_points = max(leg.mae_points, high - leg.entry_price)

    def close_leg(self, *, price: float, timestamp_utc: str, reason: str) -> Trade:
        leg = self._open_leg
        if leg is None:
            raise TradovateStateError("exit fill with no open leg")
        direction = 1 if leg.side == "long" else -1
        gross_points = (price - leg.entry_price) * direction
        gross_pnl = gross_points * self._dollar_point_value * leg.quantity
        commission = self._commission_round_trip * leg.quantity
        trade = Trade(
            symbol=leg.symbol,
            side=leg.side,
            quantity=leg.quantity,
            entry_timestamp_utc=leg.entry_timestamp_utc,
            entry_price=leg.entry_price,
            exit_timestamp_utc=timestamp_utc,
            exit_price=price,
            exit_reason=reason,
            stop_price=leg.stop_price,
            gross_points=gross_points,
            gross_pnl=gross_pnl,
            commission=commission,
            net_pnl=gross_pnl - commission,
            mfe_points=leg.mfe_points,
            mae_points=leg.mae_points,
            session_date=leg.session_date,
            ambiguous_exit=False,
        )
        self._trades.append(trade)
        self._open_leg = None
        return trade

    def realized_session_pnl(self, session_date: str) -> float:
        return sum(t.net_pnl for t in self._trades if t.session_date == session_date)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tradovate_ledger.py -q`
Expected: 5 passed. If the parity test fails on `entry_timestamp_utc` or `session_date`, read the actual `sim_trade` values and check the sim fills the pending entry stamped with the FILL bar's timestamp (`bar2`); fix the test's expectation only if the sim's actual field disagrees with the plan's comment — never fudge the ledger to make it pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q` — expected 262 passed, 3 skipped.

```bash
git add src/full_python/tradovate/ledger.py tests/test_tradovate_ledger.py
git commit -m "feat: add fill-derived trade pairing ledger for the Tradovate adapter"
```

---

### Task 3: Submitted-order map + ingest validation (gap #6)

**Files:**
- Modify: `src/full_python/tradovate/broker.py` (bulk of the class)
- Test: `tests/test_tradovate_broker.py` (update existing + add new)

**Interfaces:**
- Consumes: `FillPairingLedger` (Task 2), `TradovateStateError` (Task 1).
- Produces (relied on by Tasks 4-8): `SubmittedOrder` dataclass; `TradovateBroker._orders: dict[str, SubmittedOrder]`; `_ingest_fill`/`_on_entry_fill`/`_on_exit_fill` hooks; broker-construction validation (`dollar_point_value` required; `order_enabled` requires `daily_loss_limit` AND `flatten_enabled`); raw-event kinds `fill|partial_fill|position|reject|cancel`. Roles: `"entry" | "protective_stop" | "exit"`.
- Note: `position` raw events no longer SET position; in this task they are parsed and stored for Task 7's reconciliation (temporarily no-op if matching). Fill-derived position is truth from this task onward.

- [ ] **Step 1: Update the test fakes and helpers**

In `tests/test_tradovate_broker.py`, replace `FakeRestClient` and `_cfg` with:

```python
class FakeRestClient:
    def __init__(self):
        self.placed = []
        self.canceled = []
        self.liquidations = []
        # queue of order_place responses; each call pops one (default ids 101, 102, ...)
        self.order_place_responses = []
        self._auto_id = 100
        self.order_place_error = None      # set to an exception to make order_place raise
        self.order_cancel_error = None     # set to an exception to make order_cancel raise

    def order_place(self, body):
        if self.order_place_error is not None:
            error, self.order_place_error = self.order_place_error, None
            raise error
        self.placed.append(body)
        if self.order_place_responses:
            return self.order_place_responses.pop(0)
        self._auto_id += 1
        return {"orderId": self._auto_id}

    def order_cancel(self, body):
        if self.order_cancel_error is not None:
            error, self.order_cancel_error = self.order_cancel_error, None
            raise error
        self.canceled.append(body)
        return {}

    def order_liquidate_position(self, body):
        self.liquidations.append(body)
        self._auto_id += 1
        return {"orderId": self._auto_id}


def _cfg(order_enabled=False, flatten_enabled=False, daily_loss_limit=1000.0):
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT,
        account_spec="DEMO123",
        account_id=456,
        root_symbol="NQ",
        order_enabled=order_enabled,
        flatten_enabled=flatten_enabled,
        dollar_point_value=20.0,
        commission_per_contract_round_trip=1.0,
        daily_loss_limit=daily_loss_limit,
    )
```

Add two shared helpers after `_entry_result`:

```python
def _fill_event(order_id, action="Buy", qty=1, price=100.25, ts="2026-07-07T14:32:00Z", reason=""):
    return TradovateRawEvent(kind="fill", data={
        "orderId": order_id, "action": action, "qty": qty,
        "price": price, "timestamp": ts, "reason": reason,
    })


def _entered_broker(rest=None, side="buy", price=100.25, config=None):
    """Broker with a filled entry: order 101 placed, filled at `price`."""
    rest = rest or FakeRestClient()
    broker = TradovateBroker(config or _cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar, side=side))
    broker.ingest_raw_event(_fill_event(101, action="Buy" if side == "buy" else "Sell", price=price))
    return broker, rest
```

- [ ] **Step 2: Update the existing tests this task breaks**

Replace these four tests in place:

```python
def test_orders_enabled_places_automated_market_order_and_emits_ack():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    assert rest.placed == [{
        "accountSpec": "DEMO123",
        "accountId": 456,
        "action": "Buy",
        "symbol": "NQ",
        "orderQty": 1,
        "orderType": "Market",
        "isAutomated": True,
    }]
    assert broker.poll_events() == [Acked(order_id="101")]


def test_live_enabled_entry_requires_stop_price_metadata():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()

    with pytest.raises(TradovateOrderSafetyError, match="stop_price"):
        broker.apply_strategy_result(bar, _session(bar), _entry_result(bar, metadata={}))

    assert rest.placed == []


def test_fill_raw_event_updates_position_and_emits_filled():
    broker, _rest = _entered_broker()

    assert broker.position == BrokerPosition(side="long", quantity=1, entry_price=100.25)
    filled = [e for e in broker.poll_events() if isinstance(e, Filled)]
    assert filled == [Filled(
        order_id="101",
        side="buy",
        quantity=1,
        price=100.25,
        timestamp_utc="2026-07-07T14:32:00Z",
        reason="",
    )]


def test_partial_fill_raw_event_maps_to_partial_filled():
    broker, _rest = _entered_broker()

    broker.ingest_raw_event(TradovateRawEvent(
        kind="partial_fill",
        data={
            "orderId": 102,
            "action": "Sell",
            "qty": 1,
            "remaining": 2,
            "price": 100.25,
            "timestamp": "2026-07-07T14:32:00Z",
        },
    ))

    partials = [e for e in broker.poll_events() if isinstance(e, PartialFilled)]
    assert partials == [PartialFilled(
        order_id="102",
        side="sell",
        quantity=1,
        remaining=2,
        price=100.25,
        timestamp_utc="2026-07-07T14:32:00Z",
    )]
```

(Order id 102 is the protective stop Task 4 will submit; until Task 4 lands, `_entered_broker` produces no order 102, so this partial-fill test must use the entry id 101 temporarily — write it with `"orderId": 101` in this task and flip it to 102 in Task 4, where the stop order exists. The same applies to `test_partial_fill_event_from_broker_is_fatal_for_order_state_machine` below.)

Replace the two position-event/flatten tests with placeholders that Task 5/7 finalize (position events no longer set position):

```python
def test_position_snapshot_matching_fill_derived_state_passes():
    broker, _rest = _entered_broker()

    broker.ingest_raw_event(TradovateRawEvent(
        kind="position",
        data={"side": "long", "qty": 1, "price": 100.25},
    ))  # matching snapshot: no exception

    assert broker.position == BrokerPosition(side="long", quantity=1, entry_price=100.25)
```

Delete `test_position_raw_event_accepts_plan_price_key` (position events no longer set position — replaced by the snapshot test above). Rewrite the two flatten tests to build position via `_entered_broker()`:

```python
def test_flatten_disabled_raises_and_does_not_call_liquidation():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(flatten_enabled=False), rest)

    with pytest.raises(TradovateOrderSafetyError, match="flatten_disabled"):
        broker.flatten(_bar(), "supervisor_halt")

    assert rest.liquidations == []


def test_flatten_enabled_with_position_calls_liquidate_position():
    broker, rest = _entered_broker()

    broker.flatten(_bar(), "supervisor_halt")

    assert rest.liquidations == [{
        "accountSpec": "DEMO123",
        "accountId": 456,
        "symbol": "NQ",
        "admin": False,
    }]
```

Update the Task-6-era integration test to use a known order id:

```python
def test_partial_fill_event_from_broker_is_fatal_for_order_state_machine():
    broker, _rest = _entered_broker()
    broker.poll_events()  # drain entry lifecycle events
    broker.ingest_raw_event(TradovateRawEvent(
        kind="partial_fill",
        data={
            "orderId": 101,
            "action": "Sell",
            "qty": 1,
            "remaining": 1,
            "price": 100.25,
            "timestamp": "2026-07-07T14:32:00Z",
        },
    ))

    sm = OrderStateMachine()
    with pytest.raises(ExecutionInvariantError, match="partial fill not modeled"):
        for event in broker.poll_events():
            sm.on_event(event)
```

- [ ] **Step 3: Write the new failing tests for gap #6**

Append:

```python
def test_fill_for_unknown_order_id_raises_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker = TradovateBroker(_cfg(), FakeRestClient())

    with pytest.raises(TradovateStateError, match="unknown order id 999"):
        broker.ingest_raw_event(_fill_event(999))


def test_duplicate_fill_for_same_order_id_raises_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()

    with pytest.raises(TradovateStateError, match="duplicate fill"):
        broker.ingest_raw_event(_fill_event(101))


def test_entry_fill_while_position_open_raises_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))  # second entry order
    acks = [e for e in broker.poll_events() if isinstance(e, Acked)]

    with pytest.raises(TradovateStateError, match="position is already open"):
        broker.ingest_raw_event(_fill_event(int(acks[-1].order_id)))


def test_reject_and_cancel_for_unknown_order_ids_raise_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker = TradovateBroker(_cfg(), FakeRestClient())

    with pytest.raises(TradovateStateError, match="unknown order id"):
        broker.ingest_raw_event(TradovateRawEvent(kind="reject", data={"orderId": 5, "reason": "x"}))
    with pytest.raises(TradovateStateError, match="unknown order id"):
        broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 6}))


def test_broker_requires_dollar_point_value_and_live_pairing():
    from full_python.tradovate.errors import TradovateConfigError

    bare = TradovateAdapterConfig(environment=DEMO_ENVIRONMENT, account_spec="D", account_id=1)
    with pytest.raises(TradovateConfigError, match="dollar_point_value"):
        TradovateBroker(bare, FakeRestClient())

    with pytest.raises(TradovateConfigError, match="daily_loss_limit"):
        TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True, daily_loss_limit=None), FakeRestClient())

    with pytest.raises(TradovateConfigError, match="flatten_enabled"):
        TradovateBroker(_cfg(order_enabled=True, flatten_enabled=False), FakeRestClient())
```

(The exit-fill quantity-mismatch test lands in Task 5, where the exit path exists.)

- [ ] **Step 4: Run tests to verify the new ones fail**

Run: `python3 -m pytest tests/test_tradovate_broker.py -q`
Expected: failures across the board (constructor validation missing, unknown-id fills currently accepted).

- [ ] **Step 5: Implement in `src/full_python/tradovate/broker.py`**

Replace the imports block and everything from `@dataclass(frozen=True)\nclass TradovateRawEvent` through the end of the `TradovateBroker` class with the following (keep the module docstring for now — Task 9 rewrites it; keep the module-level helper functions `_action_from_side`, `_side_from_action`, `_fill_from_data`, `_partial_fill_from_data` as they are; `_position_from_data` is replaced in Task 7):

```python
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
        # result.exits handled in Task 5; result.stop_updates deliberately
        # never applied (production policy freezes stops at entry --
        # PositionEngine logs them applied=False and this adapter matches).
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
        # Protective stop submission lands in Task 4.

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
        # Protective-stop rejection handling (flatten + fatal) lands in Task 4.

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
```

Add the `_positions_match` helper next to the existing module-level helpers:

```python
def _positions_match(
    reported: Optional[BrokerPosition], derived: Optional[BrokerPosition]
) -> bool:
    if reported is None or derived is None:
        return reported is None and derived is None
    # entry price is NOT compared: broker netPrice averaging legitimately
    # differs from our fill price; side+quantity define position identity.
    return reported.side == derived.side and reported.quantity == derived.quantity
```

- [ ] **Step 6: Run the broker tests, then the full suite**

Run: `python3 -m pytest tests/test_tradovate_broker.py -q` — expected: all pass.
Run: `python3 -m pytest -q` — expected: no regressions elsewhere (the feed/ws/config suites don't construct `TradovateBroker`).

- [ ] **Step 7: Commit**

```bash
git add src/full_python/tradovate/broker.py tests/test_tradovate_broker.py
git commit -m "feat: submitted-order map and halt-on-unknown broker event validation"
```

---

### Task 4: Broker-held protective stop (gap #4)

**Files:**
- Modify: `src/full_python/tradovate/broker.py`
- Test: `tests/test_tradovate_broker.py`

**Interfaces:**
- Consumes: `_on_entry_fill`, `SubmittedOrder`, `_ingest_reject` (Task 3).
- Produces: `_submit_protective_stop(fill, entry_order)`, `_emergency_flatten(symbol)`, `_cancel_working_orders_best_effort()`; `self._working_stop_id` set on stop submission. Protective-stop `reason` is `"stop"` (matches the sim's stop-exit reason).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tradovate_broker.py`:

```python
def test_entry_fill_submits_protective_stop_at_frozen_price():
    broker, rest = _entered_broker()

    stop_bodies = [b for b in rest.placed if b.get("orderType") == "Stop"]
    assert stop_bodies == [{
        "accountSpec": "DEMO123",
        "accountId": 456,
        "action": "Sell",           # opposite of the long entry
        "symbol": "NQ",
        "orderQty": 1,
        "orderType": "Stop",
        "stopPrice": 95.0,          # frozen at the entry intent's stop_price
        "isAutomated": True,
    }]
    acks = [e for e in broker.poll_events() if isinstance(e, Acked)]
    assert [a.order_id for a in acks] == ["101", "102"]  # entry, then stop


def test_protective_stop_rest_failure_flattens_and_raises():
    from full_python.tradovate.errors import TradovateRequestError, TradovateStateError

    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))
    rest.order_place_error = TradovateRequestError("boom")

    with pytest.raises(TradovateStateError, match="protective stop"):
        broker.ingest_raw_event(_fill_event(101))

    assert rest.liquidations != []   # emergency flatten was requested


def test_protective_stop_rejection_flattens_and_raises():
    from full_python.tradovate.errors import TradovateStateError

    broker, rest = _entered_broker()   # stop order 102 is working

    with pytest.raises(TradovateStateError, match="protective stop"):
        broker.ingest_raw_event(TradovateRawEvent(
            kind="reject", data={"orderId": 102, "reason": "risk_rules"},
        ))

    assert rest.liquidations != []


def test_reject_event_for_known_entry_emits_rejected():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))

    broker.ingest_raw_event(TradovateRawEvent(
        kind="reject", data={"orderId": 101, "reason": "outside_market_hours"},
    ))

    rejects = [e for e in broker.poll_events() if isinstance(e, Rejected)]
    assert rejects == [Rejected(order_id="101", reason="outside_market_hours")]
    assert broker.position is None
    assert rest.liquidations == []   # entry rejection needs no flatten
```

Also flip the two partial-fill tests from order id 101 to 102 (the stop now exists — a partial fill on the protective stop is the realistic fatal case):

- In `test_partial_fill_raw_event_maps_to_partial_filled` and `test_partial_fill_event_from_broker_is_fatal_for_order_state_machine`, change `"orderId": 101` to `"orderId": 102` and the expected `PartialFilled(order_id="102", ...)` accordingly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tradovate_broker.py -q`
Expected: the four new tests fail (no stop submitted; rejection not fatal); the flipped partial-fill tests fail (order 102 unknown).

- [ ] **Step 3: Implement**

In `broker.py`, replace the final line of `_on_entry_fill` (`# Protective stop submission lands in Task 4.`) with:

```python
        self._submit_protective_stop(fill, order)
```

Add the three methods after `_on_entry_fill`:

```python
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
```

In `_ingest_reject`, replace the trailing comment line with:

```python
        if order.role == ROLE_PROTECTIVE_STOP:
            if order.order_id == self._working_stop_id:
                self._working_stop_id = None
            self._emergency_flatten(order.symbol)
            raise TradovateStateError(
                f"protective stop {order.order_id} rejected; emergency flatten requested"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tradovate_broker.py -q` — expected: all pass.
Note `test_entry_fill_submits_protective_stop_at_frozen_price` also locks the ack ordering ["101", "102"]; if `_entered_broker` drains events internally, remove any `poll_events()` call inside the helper.

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q`

```bash
git add src/full_python/tradovate/broker.py tests/test_tradovate_broker.py
git commit -m "feat: broker-held frozen protective stop with flatten-and-halt on failure"
```

---

### Task 5: Exit path + flatten rework (gap #5)

**Files:**
- Modify: `src/full_python/tradovate/broker.py`
- Test: `tests/test_tradovate_broker.py`

**Interfaces:**
- Consumes: order map + stop machinery (Tasks 3-4).
- Produces: `apply_strategy_result` processes `result.exits` (cancel stop → market close, role `"exit"`, reason from the `ExitDecision`); `flatten()` cancels working orders best-effort, then liquidates AND records the liquidation order in the map (so its fill is a known id).

- [ ] **Step 1: Write the failing tests**

Append:

```python
def _exit_result(bar=None, reason="atf_flip"):
    from full_python.models import ExitDecision
    bar = bar or _bar()
    return StrategyResult(exits=(
        ExitDecision(timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, reason=reason),
    ))


def test_strategy_exit_cancels_stop_then_market_closes():
    broker, rest = _entered_broker()
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _exit_result(bar))

    assert rest.canceled == [{"orderId": 102}]
    close_bodies = [b for b in rest.placed if b["orderType"] == "Market"][1:]
    assert close_bodies == [{
        "accountSpec": "DEMO123",
        "accountId": 456,
        "action": "Sell",
        "symbol": "NQ",
        "orderQty": 1,
        "orderType": "Market",
        "isAutomated": True,
    }]
    # exit fill closes the trade with the strategy's reason
    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))
    broker.ingest_raw_event(_fill_event(103, action="Sell", price=101.25,
                                        ts="2026-07-07T14:33:00Z"))
    assert broker.position is None


def test_strategy_exit_while_flat_is_a_no_op():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()

    broker.apply_strategy_result(bar, _session(bar), _exit_result(bar))

    assert rest.canceled == [] and rest.placed == []


def test_strategy_exit_stop_cancel_failure_halts_without_close_order():
    from full_python.tradovate.errors import TradovateRequestError, TradovateStateError

    broker, rest = _entered_broker()
    rest.order_cancel_error = TradovateRequestError("cancel refused")
    bar = _bar()

    with pytest.raises(TradovateStateError, match="cancel protective stop"):
        broker.apply_strategy_result(bar, _session(bar), _exit_result(bar))

    # No market close was submitted: the stop still protects the position,
    # and two live closing orders must never coexist.
    assert [b for b in rest.placed if b["orderType"] == "Market"] == [rest.placed[0]]


def test_exit_fill_quantity_mismatch_raises_state_error():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()

    with pytest.raises(TradovateStateError, match="quantity"):
        broker.ingest_raw_event(_fill_event(102, action="Sell", qty=3))


def test_cancel_event_for_known_order_emits_canceled():
    from full_python.execution.broker_protocol import Canceled

    broker, _rest = _entered_broker()

    broker.ingest_raw_event(TradovateRawEvent(kind="cancel", data={"orderId": 102}))

    cancels = [e for e in broker.poll_events() if isinstance(e, Canceled)]
    assert cancels == [Canceled(order_id="102")]


def test_flatten_while_flat_is_a_no_op():
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(flatten_enabled=True), rest)

    broker.flatten(_bar(), "supervisor_halt")

    assert rest.liquidations == []


def test_flatten_while_short_cancels_stop_then_liquidates():
    broker, rest = _entered_broker(side="sell")

    broker.flatten(_bar(), "daily_limit")

    assert rest.canceled == [{"orderId": 102}]
    assert len(rest.liquidations) == 1
    # the liquidation order is registered: its fill is a KNOWN id
    liq_id = 103
    broker.ingest_raw_event(_fill_event(liq_id, action="Buy", price=99.0,
                                        ts="2026-07-07T14:34:00Z"))
    assert broker.position is None
```

Also update `test_flatten_enabled_with_position_calls_liquidate_position` (Task 3 version): the flatten now also cancels the working stop first — add `assert rest.canceled == [{"orderId": 102}]` before the liquidation assertion.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tradovate_broker.py -q`
Expected: new tests fail (`result.exits` still dropped; flatten doesn't cancel or register the liquidation).

- [ ] **Step 3: Implement**

In `apply_strategy_result`, insert BEFORE the `for intent in result.order_intents:` loop:

```python
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
```

Add the strict cancel helper after `_cancel_working_orders_best_effort`:

```python
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
```

Replace `flatten` with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tradovate_broker.py -q` — expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q`

```bash
git add src/full_python/tradovate/broker.py tests/test_tradovate_broker.py
git commit -m "feat: strategy exit path and order-registered flatten for Tradovate broker"
```

---

### Task 6: Fill-derived session P&L, DLL, and trades (gaps #1, #2, #3)

**Files:**
- Modify: `src/full_python/tradovate/broker.py`
- Test: `tests/test_tradovate_broker.py`

**Interfaces:**
- Consumes: `FillPairingLedger` (already wired), flatten (Task 5).
- Produces: real `process_bar_open` (returns realized + unrealized-gross-at-close; sets `daily_limit_hit`; breach → cancel-stop-and-flatten), real `trades` property, session-rollover reset, `TradovateStateError` on rollover with open position/orders or on breach with flatten disabled.

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_process_bar_open_returns_realized_plus_unrealized_gross():
    broker, _rest = _entered_broker(price=100.0)  # long 1 @ 100
    bar = _bar()  # close 100.25

    session_pnl = broker.process_bar_open(bar, _session(bar))

    assert session_pnl == pytest.approx(0.25 * 20.0)  # unrealized gross only
    assert broker.daily_limit_hit is False


def test_realized_losses_accumulate_into_session_pnl_and_trades():
    broker, _rest = _entered_broker(price=100.0)
    # stop fills 30pts against: -600 gross, -601 net
    broker.ingest_raw_event(_fill_event(102, action="Sell", price=70.0,
                                        ts="2026-07-07T14:35:00Z"))
    bar = _bar()

    session_pnl = broker.process_bar_open(bar, _session(bar))

    assert session_pnl == pytest.approx(-601.0)
    assert len(broker.trades) == 1
    assert broker.trades[0].net_pnl == pytest.approx(-601.0)
    assert broker.trades[0].exit_reason == "stop"
    assert broker.trades[0].session_date == "2026-07-07"
    assert broker.daily_limit_hit is False  # -601 > -1000


def test_daily_loss_breach_sets_flag_and_flattens_open_position():
    broker, rest = _entered_broker(price=100.0)
    # first round trip: -601 net realized
    broker.ingest_raw_event(_fill_event(102, action="Sell", price=70.0,
                                        ts="2026-07-07T14:35:00Z"))
    # second entry, long 1 @ 100 (order 103 entry, 104 stop)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))
    broker.ingest_raw_event(_fill_event(103, price=100.0, ts="2026-07-07T14:36:00Z"))
    # bar closes 25pts against: unrealized -500 -> session -1101 <= -1000
    losing_bar = MarketBar(timestamp_utc="2026-07-07T14:37:00Z", symbol="NQ",
                           open=100.0, high=100.0, low=75.0, close=75.0, volume=1.0)

    session_pnl = broker.process_bar_open(losing_bar, _session(losing_bar))

    assert session_pnl == pytest.approx(-601.0 - 500.0)
    assert broker.daily_limit_hit is True
    assert len(rest.liquidations) == 1          # DLL breach flattened
    assert {"orderId": 104} in rest.canceled    # stop canceled first


def test_daily_loss_breach_with_flatten_disabled_halts():
    from full_python.tradovate.errors import TradovateStateError

    # orders disabled so the flag pairing rule allows flatten_enabled=False;
    # build the losing position via direct fill ingestion on a manual order.
    rest = FakeRestClient()
    broker = TradovateBroker(_cfg(order_enabled=True, flatten_enabled=True), rest)
    bar = _bar()
    broker.apply_strategy_result(bar, _session(bar), _entry_result(bar))
    broker.ingest_raw_event(_fill_event(101, price=100.0))
    # simulate a misconfigured runtime by flipping the internal config object
    # is not possible (frozen); instead assert the code path via a broker
    # whose position was built while flatten was enabled and a NEW broker is
    # not constructible in that state -- so this test pins the guard directly:
    broker._config = _cfg(order_enabled=False, flatten_enabled=False)
    losing_bar = MarketBar(timestamp_utc="2026-07-07T14:37:00Z", symbol="NQ",
                           open=100.0, high=100.0, low=40.0, close=40.0, volume=1.0)

    with pytest.raises(TradovateStateError, match="flatten"):
        broker.process_bar_open(losing_bar, _session(losing_bar))


def test_session_rollover_resets_daily_limit_when_flat():
    broker, rest = _entered_broker(price=100.0)
    # lose big enough to breach: stop fill 60pts against = -1201 net
    broker.ingest_raw_event(_fill_event(102, action="Sell", price=40.0,
                                        ts="2026-07-07T14:35:00Z"))
    bar = _bar()
    broker.process_bar_open(bar, _session(bar))
    assert broker.daily_limit_hit is True
    broker.note_bar_processed(bar, _session(bar))

    next_day = MarketBar(timestamp_utc="2026-07-08T14:31:00Z", symbol="NQ",
                         open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0)
    session_pnl = broker.process_bar_open(next_day, _session(next_day))

    assert broker.daily_limit_hit is False
    assert session_pnl == 0.0  # yesterday's realized loss does not carry over


def test_session_rollover_with_open_position_halts():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker(price=100.0)
    bar = _bar()
    broker.process_bar_open(bar, _session(bar))
    broker.note_bar_processed(bar, _session(bar))
    next_day = MarketBar(timestamp_utc="2026-07-08T14:31:00Z", symbol="NQ",
                         open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0)

    with pytest.raises(TradovateStateError, match="session rollover"):
        broker.process_bar_open(next_day, _session(next_day))
```

Note on `test_daily_loss_breach_with_flatten_disabled_halts`: reassigning `broker._config` is deliberate test surgery on a private attribute to reach an otherwise-unconstructible state (the constructor forbids order_enabled without flatten_enabled). Keep the comment in the test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tradovate_broker.py -q`
Expected: new tests fail (`process_bar_open` returns 0.0; `trades` returns []).

- [ ] **Step 3: Implement**

Replace the `process_bar_open` stub with:

```python
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
```

Replace the `trades` property stub with:

```python
    @property
    def trades(self) -> list[Trade]:
        return self._fill_ledger.trades
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tradovate_broker.py -q` — expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python3 -m pytest -q`

```bash
git add src/full_python/tradovate/broker.py tests/test_tradovate_broker.py
git commit -m "feat: fill-derived session P&L, live DLL, and real trades for Tradovate broker"
```

---

### Task 7: Position reconciliation + remaining WS/feed matrix rows

**Files:**
- Modify: `src/full_python/tradovate/broker.py` (`_position_from_data` + `reconcile_rest_positions`)
- Test: `tests/test_tradovate_broker.py`, `tests/test_tradovate_ws.py`, `tests/test_tradovate_feed.py`

**Interfaces:**
- Produces: `_position_from_data` returns `None` for flat snapshots (`qty`/`netPos` of 0, or `side == "flat"`); `TradovateBroker.reconcile_rest_positions(positions: list[dict]) -> None` (netPos-summing REST cross-check, raises `TradovateStateError` on mismatch).

- [ ] **Step 1: Write the failing broker tests**

Append to `tests/test_tradovate_broker.py`:

```python
def test_position_snapshot_with_position_while_fill_derived_flat_raises():
    from full_python.tradovate.errors import TradovateStateError

    broker = TradovateBroker(_cfg(), FakeRestClient())

    with pytest.raises(TradovateStateError, match="contradicts"):
        broker.ingest_raw_event(TradovateRawEvent(
            kind="position", data={"side": "long", "qty": 1, "price": 100.25},
        ))


def test_flat_position_snapshot_while_fill_derived_open_raises():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()

    with pytest.raises(TradovateStateError, match="contradicts"):
        broker.ingest_raw_event(TradovateRawEvent(
            kind="position", data={"side": "flat", "qty": 0},
        ))


def test_rest_position_snapshot_disagreement_raises():
    from full_python.tradovate.errors import TradovateStateError

    broker, _rest = _entered_broker()   # fill-derived: long 1

    broker.reconcile_rest_positions([{"netPos": 1, "netPrice": 100.5}])  # match: ok

    with pytest.raises(TradovateStateError, match="REST position"):
        broker.reconcile_rest_positions([{"netPos": -2, "netPrice": 100.5}])

    with pytest.raises(TradovateStateError, match="REST position"):
        broker.reconcile_rest_positions([])  # broker flat, we are long
```

- [ ] **Step 2: Write the failing ws/feed tests**

Append to `tests/test_tradovate_ws.py`:

```python
def test_ws_authorize_failure_status_raises() -> None:
    transport = FakeWebSocketTransport(['a[{"s":401,"i":0,"d":{}}]'])
    client = TradovateWebSocketClient(transport)

    with pytest.raises(TradovateWebSocketError, match="401"):
        client.authorize("bad-token")


def test_ws_close_frame_while_waiting_for_response_raises() -> None:
    transport = FakeWebSocketTransport(["c"])
    client = TradovateWebSocketClient(transport)

    with pytest.raises(TradovateWebSocketError, match="closed"):
        client.request("order/placeorder", {"orderQty": 1})
```

(If `parse_message("c")` is not the close frame in `ws.py`, read `parse_message` and use the actual close frame literal; the assertion on "closed" stands.)

Append to `tests/test_tradovate_feed.py`:

```python
def test_malformed_chart_bar_raises_value_error() -> None:
    import pytest

    bad_bar = {"timestamp": "2025-11-03T14:31:00.000Z", "open": "100.0"}  # missing high/low/close

    with pytest.raises(ValueError, match="Chart bar missing"):
        chart_bar_to_vendor_bar(bad_bar, symbol="NQZ5")
```

(Match `chart_bar_to_vendor_bar`'s actual signature from the existing conversion tests at the top of the file — reuse their call shape.)

- [ ] **Step 3: Run tests to verify the broker/REST ones fail**

Run: `python3 -m pytest tests/test_tradovate_broker.py tests/test_tradovate_ws.py tests/test_tradovate_feed.py -q`
Expected: broker tests fail (`side: "flat"` raises `unsupported_position_side`; no `reconcile_rest_positions`). The ws/feed tests may already PASS (the error paths exist; they were untested) — that is fine: they pin the rows.

- [ ] **Step 4: Implement**

Replace `_position_from_data` in `broker.py`:

```python
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
```

Add to `TradovateBroker` after `_reconcile_position_event`:

```python
    def reconcile_rest_positions(self, positions: list) -> None:
        """Cross-check a REST /position/list snapshot against fill-derived
        truth (Failure Matrix: REST vs WebSocket disagreement -> halt)."""
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
```

- [ ] **Step 5: Run tests to verify they pass, full suite, commit**

Run: `python3 -m pytest tests/test_tradovate_broker.py tests/test_tradovate_ws.py tests/test_tradovate_feed.py -q` then `python3 -m pytest -q`

```bash
git add src/full_python/tradovate/broker.py tests/test_tradovate_broker.py tests/test_tradovate_ws.py tests/test_tradovate_feed.py
git commit -m "feat: position snapshot reconciliation and remaining failure-matrix coverage"
```

---

### Task 8: LiveLoop integration

**Files:**
- Test: `tests/test_tradovate_live_loop.py` (new)

**Interfaces:**
- Consumes: everything above; `LiveLoop`, `RiskSupervisor`, `EventLedger` (existing, untouched).
- Produces: proof that (a) realized losses trip the strategy-facing DLL and the supervisor through the real loop, and (b) `TradovateStateError` halts `LiveLoop` as an invariant violation without flatten.

- [ ] **Step 1: Write the integration tests**

Create `tests/test_tradovate_live_loop.py`:

```python
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
        self.liquidations.append(body)
        self._auto_id += 1
        return {"orderId": self._auto_id}


def _bar(ts: str, price: float) -> MarketBar:
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=price, high=price,
                     low=price, close=price, volume=1.0)


def _fill(order_id: int, action: str, price: float, ts: str) -> TradovateRawEvent:
    return TradovateRawEvent(kind="fill", data={
        "orderId": order_id, "action": action, "qty": 1,
        "price": price, "timestamp": ts, "reason": "",
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
                    timestamp_utc=bar.timestamp_utc, symbol="NQ", side="buy",
                    quantity=1, reason="scripted", metadata={"stop_price": bar.close - 30.0},
                ),
            ))
        return StrategyResult()


def _cfg() -> TradovateAdapterConfig:
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT, account_spec="DEMO123", account_id=456,
        root_symbol="NQ", order_enabled=True, flatten_enabled=True,
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
```

- [ ] **Step 2: Run the tests**

Run: `python3 -m pytest tests/test_tradovate_live_loop.py -q`
Expected: both pass. If the first fails on event ordering, check where fills are ingested relative to `process_bar_open` (the scripted source ingests BEFORE the bar is yielded, i.e. before that bar's `process_bar_open` — matching real life, where fills land between bars). If `EventLedger`/`EventType` imports differ, mirror the imports used in `tests/test_live_loop_identity.py`.

- [ ] **Step 3: Full suite, then commit**

Run: `python3 -m pytest -q`

```bash
git add tests/test_tradovate_live_loop.py
git commit -m "test: LiveLoop integration for Tradovate DLL, supervisor, and halt paths"
```

---

### Task 9: Documentation closure

**Files:**
- Modify: `src/full_python/tradovate/broker.py` (module docstring)
- Modify: `docs/superpowers/specs/2026-07-07-tradovate-adapter-design.md` (amendment)
- Modify: `docs/superpowers/specs/2026-07-10-tradovate-gap-closure-design.md` (matrix table)

- [ ] **Step 1: Rewrite the broker module docstring**

Replace the entire module docstring of `broker.py` (the six-gap warning block) with:

```python
"""Tradovate broker adapter.

Implements execution.broker_protocol.Broker against the Tradovate REST/WS
surface, offline-first (all tests run on fake transports). Safety model:

- Submitted-order map: every order this adapter places is recorded; any
  fill/cancel/reject for an unknown order id (platform liquidation,
  manual intervention, stale message) raises TradovateStateError, which
  subclasses ExecutionInvariantError so LiveLoop halts WITHOUT flatten
  (position truth unknown). Duplicate fills likewise halt.
- Broker-held protective stop: submitted immediately on every entry fill
  at the entry's frozen stop_price, never modified afterwards (production
  policy freezes stops at entry; result.stop_updates are deliberately not
  applied, matching PositionEngine). If the stop cannot be confirmed the
  adapter flattens and raises. No OCO: the production strategy emits no
  target_price (N/A-by-design in the Failure Matrix).
- Exits: result.exits cancel the working stop, then market-close. A stop
  cancel failure halts WITHOUT submitting the close (two live closing
  orders must never coexist). flatten() cancels working orders
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

The six 2026-07-10 tracked gaps are CLOSED as of the 2026-07-10 gap-
closure spec (docs/superpowers/specs/2026-07-10-tradovate-gap-closure-
design.md); each is pinned by the Failure Matrix tests listed there.
Partial fills remain fatal by design (OrderStateMachine raises).
"""
```

Also delete the now-stale inline `TRACKED GAP` / `STUB` comments if any survived Tasks 3-6.

- [ ] **Step 2: Update the parent spec amendment**

In `docs/superpowers/specs/2026-07-07-tradovate-adapter-design.md`, at the END of the "Amendment 2026-07-10 — Tracked Risk-Management Gaps" section (after the paragraph about the Failure Matrix count), add:

```markdown
**Closure (2026-07-10, same day):** all six gaps were closed by the
gap-closure sub-spec (`2026-07-10-tradovate-gap-closure-design.md`) —
fill-derived ledger accounting (gaps 1-3), broker-held frozen protective
stop with flatten-and-halt on confirmation failure (gap 4), the
cancel-then-close exit path (gap 5), and the submitted-order map with
halt-on-unknown/duplicate events (gap 6). The Failure Matrix stands at
28/28: 27 rows test-covered, and the stop+target OCO row recorded
N/A-by-design (the production strategy emits no `target_price`). The
binding above is amended accordingly: `order_enabled=True` /
`flatten_enabled=True` remain forbidden against a funded account until
the sub-project 4 gates (demo observe → demo order test → pilot
checklist) are passed — the gap list itself is no longer the blocker.
```

- [ ] **Step 3: Record final matrix status in the gap-closure spec**

In `docs/superpowers/specs/2026-07-10-tradovate-gap-closure-design.md`, under the Testing section's Failure Matrix paragraph, append one line:

```markdown
Implemented: see the row-by-row audit table in
`docs/superpowers/plans/2026-07-10-tradovate-gap-closure.md` — 27 rows
test-covered + 1 N/A-by-design as of the closure commits.
```

- [ ] **Step 4: Full suite and commit**

Run: `python3 -m pytest -q` — everything green (expected ~280 passed, 3 skipped; exact count depends on tests added above — record the real number in the commit message if it differs).

```bash
git add src/full_python/tradovate/broker.py docs/superpowers/specs/2026-07-07-tradovate-adapter-design.md docs/superpowers/specs/2026-07-10-tradovate-gap-closure-design.md
git commit -m "docs: mark all six Tradovate broker gaps closed with 28/28 failure matrix"
```

---

## Plan Self-Review

- **Spec coverage:** fill-derived ledger → T2; order map/halt-on-unknown (gap 6) → T3; protective stop + confirmation failure (gap 4) → T4; exits + stop_updates no-op + flatten (gap 5) → T5; session P&L/DLL/trades (gaps 1-3) + rollover + flatten-disabled-breach halt → T6; position-event + REST reconciliation (rows 22/23/28) and ws/feed rows (5b/6/8) → T7; LiveLoop proof + no-flatten-on-invariant → T8; docstring + parent-amendment closure + N/A-by-design OCO record → T9. Config additions + `TradovateStateError`-as-invariant → T1.
- **Type consistency:** `SubmittedOrder(order_id, role, side, quantity, symbol, stop_price, reason, status)` used identically in T3-T5; `FillPairingLedger` keyword-only API identical in T2 (definition) and T3/T6 (usage); `_positions_match` defined T3, used T7.
- **Known execution notes:** (a) T3 temporarily uses order id 101 for the partial-fill tests and T4 flips them to 102 — deliberate, keeps every task green; (b) T6's flatten-disabled-breach test reassigns `broker._config` to reach an otherwise-unconstructible state — commented in the test; (c) expected suite counts are approximate after T4+ — trust green/red, not the exact number.
```
