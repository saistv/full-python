from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MarketBar:
    timestamp_utc: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class SignalDecision:
    timestamp_utc: str
    symbol: str
    decision: str
    side: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def accepted(
        cls,
        *,
        timestamp_utc: str,
        symbol: str,
        side: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> "SignalDecision":
        return cls(
            timestamp_utc=timestamp_utc,
            symbol=symbol,
            decision="accepted",
            side=side,
            reason=reason,
            metadata={} if metadata is None else dict(metadata),
        )

    @classmethod
    def rejected(
        cls,
        *,
        timestamp_utc: str,
        symbol: str,
        side: str | None,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> "SignalDecision":
        return cls(
            timestamp_utc=timestamp_utc,
            symbol=symbol,
            decision="rejected",
            side=side,
            reason=reason,
            metadata={} if metadata is None else dict(metadata),
        )

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "decision": self.decision,
            "side": self.side,
            "reason": self.reason,
        }
        payload.update(self.metadata)
        return payload


@dataclass(frozen=True)
class OrderIntent:
    timestamp_utc: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def market_entry(
        cls,
        *,
        timestamp_utc: str,
        symbol: str,
        side: str,
        quantity: int,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> "OrderIntent":
        return cls(
            timestamp_utc=timestamp_utc,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market_entry",
            reason=reason,
            metadata={} if metadata is None else dict(metadata),
        )

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "reason": self.reason,
        }
        payload.update(self.metadata)
        return payload


@dataclass(frozen=True)
class RiskVeto:
    timestamp_utc: str
    symbol: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = {"symbol": self.symbol, "reason": self.reason}
        payload.update(self.metadata)
        return payload


@dataclass(frozen=True)
class StopUpdate:
    timestamp_utc: str
    symbol: str
    stop_price: float
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "stop_price": self.stop_price,
            "reason": self.reason,
        }
        payload.update(self.metadata)
        return payload


@dataclass(frozen=True)
class ExitDecision:
    timestamp_utc: str
    symbol: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = {"symbol": self.symbol, "reason": self.reason}
        payload.update(self.metadata)
        return payload


@dataclass(frozen=True)
class StrategyResult:
    signal: SignalDecision | None = None
    order_intents: tuple[OrderIntent, ...] = ()
    risk_vetoes: tuple[RiskVeto, ...] = ()
    stop_updates: tuple[StopUpdate, ...] = ()
    exits: tuple[ExitDecision, ...] = ()


@dataclass(frozen=True)
class Fill:
    timestamp_utc: str
    symbol: str
    side: str
    quantity: int
    price: float
    reason: str
    ambiguous: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "reason": self.reason,
            "ambiguous": self.ambiguous,
        }
        payload.update(self.metadata)
        return payload


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: str
    quantity: int
    entry_timestamp_utc: str
    entry_price: float
    exit_timestamp_utc: str
    exit_price: float
    exit_reason: str
    stop_price: float
    gross_points: float
    gross_pnl: float
    commission: float
    net_pnl: float
    mfe_points: float
    mae_points: float
    session_date: str
    ambiguous_exit: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "entry_timestamp_utc": self.entry_timestamp_utc,
            "entry_price": self.entry_price,
            "exit_timestamp_utc": self.exit_timestamp_utc,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "stop_price": self.stop_price,
            "gross_points": self.gross_points,
            "gross_pnl": self.gross_pnl,
            "commission": self.commission,
            "net_pnl": self.net_pnl,
            "mfe_points": self.mfe_points,
            "mae_points": self.mae_points,
            "session_date": self.session_date,
            "ambiguous_exit": self.ambiguous_exit,
        }
