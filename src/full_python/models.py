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
