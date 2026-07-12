from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import time
from typing import Any, Deque, Dict, Optional, Protocol

from full_python.livedata.feed import MarketDataFeed, VendorBar
from full_python.tradovate.errors import TradovateFeedError


class ChartWebSocketClient(Protocol):
    def request(self, endpoint: str, payload: Any) -> Any:
        ...

    def receive_event(self, timeout_seconds: float) -> Optional[dict]:
        ...


def _normalize_timestamp(value: str) -> str:
    normalized = value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("Invalid chart bar timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _required_float(raw: dict, key: str) -> float:
    try:
        return float(raw[key])
    except KeyError as exc:
        raise ValueError(f"Chart bar missing {key}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Chart bar has invalid {key}") from exc


def _volume(raw: dict) -> float:
    if "volume" in raw:
        try:
            return float(raw["volume"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Chart bar has invalid volume") from exc
    return _required_float(raw, "upVolume") + _required_float(raw, "downVolume")


def chart_bar_to_vendor_bar(*, symbol: str, raw: dict) -> VendorBar:
    try:
        timestamp = _normalize_timestamp(str(raw["timestamp"]))
    except KeyError as exc:
        raise ValueError("Chart bar missing timestamp") from exc
    return VendorBar(
        symbol=symbol,
        timestamp_utc=timestamp,
        open=_required_float(raw, "open"),
        high=_required_float(raw, "high"),
        low=_required_float(raw, "low"),
        close=_required_float(raw, "close"),
        volume=_volume(raw),
    )


class TradovateMarketDataFeed(MarketDataFeed):
    def __init__(
        self,
        ws: ChartWebSocketClient,
        *,
        symbol: str,
        max_ignored_events: int = 100,
        monotonic_clock=time.monotonic,
    ) -> None:
        self.ws = ws
        self.symbol = symbol
        self.max_ignored_events = max_ignored_events
        self._monotonic_clock = monotonic_clock
        self.historical_id: Optional[int] = None
        self.realtime_id: Optional[int] = None
        self._queue: Deque[VendorBar] = deque()
        self._forming_bar: Optional[VendorBar] = None
        self._last_finalized_timestamp: Optional[str] = None

    def subscribe(self, closest_timestamp: str, bars_back: int) -> None:
        response = self.ws.request(
            "md/getChart",
            {
                "symbol": self.symbol,
                "chartDescription": {
                    "underlyingType": "MinuteBar",
                    "elementSize": 1,
                    "elementSizeUnit": "UnderlyingUnits",
                    "withHistogram": False,
                },
                "timeRange": {
                    "closestTimestamp": closest_timestamp,
                    "asMuchAsElements": bars_back,
                },
            },
        )
        self.historical_id = self._optional_int(response, "historicalId")
        self.realtime_id = self._optional_int(response, "realtimeId")

    def next_bar(self, timeout_seconds: float) -> Optional[VendorBar]:
        if self._queue:
            return self._queue.popleft()

        ignored_events = 0
        deadline = self._monotonic_clock() + timeout_seconds
        while True:
            remaining = deadline - self._monotonic_clock()
            if remaining <= 0:
                return None
            event = self.ws.receive_event(remaining)
            if event is None:
                return None
            self._queue_matching_bars(event)
            if self._queue:
                return self._queue.popleft()
            ignored_events += 1
            if ignored_events >= self.max_ignored_events:
                raise TradovateFeedError("Too many Tradovate chart events without a matching bar")

    def cancel(self) -> None:
        if self.realtime_id is None:
            return
        self.ws.request("md/cancelChart", {"subscriptionId": self.realtime_id})

    def _queue_matching_bars(self, event: dict) -> None:
        if event.get("e") != "chart":
            return
        charts = event.get("d", {}).get("charts", [])
        if not isinstance(charts, list):
            return
        for chart in charts:
            if not isinstance(chart, dict):
                continue
            if not self._matches_subscription(chart):
                continue
            bars = chart.get("bars", [])
            if not isinstance(bars, list):
                continue
            for raw in bars:
                if not isinstance(raw, dict):
                    continue
                bar = chart_bar_to_vendor_bar(symbol=self.symbol, raw=raw)
                self._accept_snapshot(bar)

    def _accept_snapshot(self, bar: VendorBar) -> None:
        """Replace the forming minute; finalize only when time advances.

        Tradovate chart messages are cumulative snapshots of the latest bar.
        Repeated timestamps replace the previous snapshot. A timestamp is
        complete only after a newer timestamp arrives.
        """
        forming = self._forming_bar
        if forming is None:
            if (
                self._last_finalized_timestamp is not None
                and bar.timestamp_utc <= self._last_finalized_timestamp
            ):
                return
            self._forming_bar = bar
            return

        if bar.timestamp_utc == forming.timestamp_utc:
            self._forming_bar = bar
            return
        if bar.timestamp_utc < forming.timestamp_utc:
            # Historical and realtime subscriptions can overlap. Older bars
            # have already been finalized and must not rewrite history.
            return

        self._queue.append(forming)
        self._last_finalized_timestamp = forming.timestamp_utc
        self._forming_bar = bar

    def _matches_subscription(self, chart: Dict[str, Any]) -> bool:
        chart_id = self._chart_id(chart)
        return chart_id is not None and chart_id in (self.historical_id, self.realtime_id)

    def _chart_id(self, chart: Dict[str, Any]) -> Optional[int]:
        if "id" in chart:
            return self._coerce_int(chart.get("id"))
        if "subscriptionId" in chart:
            return self._coerce_int(chart.get("subscriptionId"))
        return None

    def _optional_int(self, value: Any, key: str) -> Optional[int]:
        if not isinstance(value, dict) or key not in value:
            return None
        return self._coerce_int(value.get(key))

    def _coerce_int(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid chart subscription id") from exc
