"""The vendor seam: finalized 1-minute bars in, nothing else about the
vendor leaks past this module. Sub-project 3's Tradovate adapter
implements MarketDataFeed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class VendorBar:
    symbol: str          # specific contract, e.g. "NQZ5"
    timestamp_utc: str    # minute-open, ISO-8601 UTC "...Z"
    open: float
    high: float
    low: float
    close: float
    volume: float


@runtime_checkable
class MarketDataFeed(Protocol):
    def next_bar(self, timeout_seconds: float) -> Optional[VendorBar]:
        """Block up to timeout_seconds; return the next finalized bar, or
        None if none arrived in the window."""
        ...
