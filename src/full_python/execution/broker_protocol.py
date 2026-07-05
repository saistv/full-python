"""Broker abstraction for the live execution stack.

Shape follows PositionEngine's proven per-bar API (design spec
Amendment 2, docs/superpowers/specs/2026-07-05-execution-core-design.md):
the broker owns fills and position truth; the loop owns strategy,
supervisor, ledger, and the bar clock. PaperBroker realizes fills via
the shared PositionEngine; the future Tradovate adapter realizes them
against the real API behind this same interface.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Union

from full_python.data.sessions import SessionInfo
from full_python.models import MarketBar, StrategyResult, Trade


@dataclass(frozen=True)
class Acked:
    order_id: str


@dataclass(frozen=True)
class Filled:
    order_id: str
    side: str  # "buy" | "sell"
    quantity: int
    price: float
    timestamp_utc: str
    reason: str


@dataclass(frozen=True)
class PartialFilled:
    order_id: str
    side: str
    quantity: int
    remaining: int
    price: float
    timestamp_utc: str


@dataclass(frozen=True)
class Rejected:
    order_id: str
    reason: str


@dataclass(frozen=True)
class Canceled:
    order_id: str


BrokerEvent = Union[Acked, Filled, PartialFilled, Rejected, Canceled]


@dataclass(frozen=True)
class BrokerPosition:
    side: str  # "long" | "short"
    quantity: int
    entry_price: float


class Broker(Protocol):
    def process_bar_open(self, bar: MarketBar, session: SessionInfo) -> float: ...

    def apply_strategy_result(
        self, bar: MarketBar, session: SessionInfo, result: StrategyResult
    ) -> None: ...

    def note_bar_processed(self, bar: MarketBar, session: SessionInfo) -> None: ...

    def close_end_of_data(self) -> None: ...

    def flatten(self, bar: MarketBar, reason: str) -> None: ...

    def poll_events(self) -> list[BrokerEvent]: ...

    @property
    def position(self) -> Optional[BrokerPosition]: ...

    @property
    def trades(self) -> list[Trade]: ...

    @property
    def daily_limit_hit(self) -> bool: ...
