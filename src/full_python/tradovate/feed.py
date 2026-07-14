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
    """Finalized one-minute bars from Tradovate's chart stream.

    Two properties of the wire protocol drive this design, and getting either
    wrong silently destroys a live session:

    1. **Snapshots, not bars.** Tradovate updates the *currently forming* bar on
       every tick. The same timestamp arrives many times with growing OHLCV; the
       last version before a newer timestamp appears is the completed bar. A busy
       minute (the 09:30 open, the only minute this strategy trades) produces
       hundreds of snapshots, so they are PROGRESS and must never be counted as
       ignorable noise.
    2. **History is unordered until ``eoh``.** The documented protocol makes no
       ordering guarantee for the historical batch: the client must gather bars,
       and sort them, until the end-of-history marker (``{id, eoh: true}``)
       arrives. Emitting eagerly while history is still streaming can drop a bar
       that arrives out of order -- and dropping warmup history means the
       strategy never warms up and silently trades nothing.

    Bars are therefore staged in a timestamp-keyed map (a repeated timestamp
    replaces its entry), and are released only once history is complete, oldest
    first, keeping the newest timestamp back as the still-forming minute.
    """

    def __init__(
        self,
        ws: ChartWebSocketClient,
        *,
        symbol: str,
        max_ignored_events: int = 100,
        monotonic_clock=time.monotonic,
        history_grace_seconds: float = 30.0,
    ) -> None:
        self.ws = ws
        self.symbol = symbol
        self.max_ignored_events = max_ignored_events
        self._monotonic_clock = monotonic_clock
        self._history_grace_seconds = history_grace_seconds
        self.historical_id: Optional[int] = None
        self.realtime_id: Optional[int] = None
        self._queue: Deque[VendorBar] = deque()
        self._staged: Dict[str, VendorBar] = {}
        self._max_seen_timestamp: Optional[str] = None
        self._last_finalized_timestamp: Optional[str] = None
        self._history_complete = True   # nothing to wait for until we subscribe
        self._subscribed_at: Optional[float] = None

    @property
    def history_complete(self) -> bool:
        return self._history_complete

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
        # A subscription with history owes us an end-of-history marker. The
        # grace clock starts on the first poll, not here, so subscribing does
        # not consume a tick.
        self._history_complete = self.historical_id is None
        self._subscribed_at = None

    def next_bar(self, timeout_seconds: float) -> Optional[VendorBar]:
        if self._queue:
            return self._queue.popleft()

        ignored_events = 0
        deadline = self._monotonic_clock() + timeout_seconds
        while True:
            now = self._monotonic_clock()
            self._expire_history_grace(now)
            if self._queue:
                return self._queue.popleft()
            remaining = deadline - now
            if remaining <= 0:
                return None
            event = self.ws.receive_event(remaining)
            if event is None:
                return None
            recognized = self._consume_event(event)
            if self._queue:
                return self._queue.popleft()
            if recognized:
                # A forming-bar snapshot or a staged history bar is progress, not
                # noise. Only the caller's timeout bounds a minute that never
                # completes -- an event counter would kill the 09:30 open.
                continue
            ignored_events += 1
            if ignored_events >= self.max_ignored_events:
                raise TradovateFeedError("Too many Tradovate chart events without a matching bar")

    def cancel(self) -> None:
        if self.realtime_id is None:
            return
        self.ws.request("md/cancelChart", {"subscriptionId": self.realtime_id})

    def _consume_event(self, event: dict) -> bool:
        """Stage any chart data in this event. Returns True if it was ours."""
        if event.get("e") != "chart":
            return False
        charts = event.get("d", {}).get("charts", [])
        if not isinstance(charts, list):
            return False
        recognized = False
        for chart in charts:
            if not isinstance(chart, dict):
                continue
            if not self._matches_subscription(chart):
                continue
            recognized = True
            if chart.get("eoh"):
                self._complete_history()
                continue
            bars = chart.get("bars", [])
            if not isinstance(bars, list):
                continue
            for raw in bars:
                if not isinstance(raw, dict):
                    continue
                try:
                    bar = chart_bar_to_vendor_bar(symbol=self.symbol, raw=raw)
                except ValueError as exc:
                    # A bar we cannot parse is data we cannot trust: halt through
                    # the LiveDataError path rather than crash out of the loop.
                    raise TradovateFeedError(f"malformed chart bar: {exc}") from exc
                self._stage(bar)
        if recognized:
            self._release_finalized()
        return recognized

    def _stage(self, bar: VendorBar) -> None:
        if (
            self._last_finalized_timestamp is not None
            and bar.timestamp_utc <= self._last_finalized_timestamp
        ):
            return  # already emitted; a stale re-send must not rewrite history
        self._staged[bar.timestamp_utc] = bar   # repeated timestamp = forming update
        if (
            self._max_seen_timestamp is None
            or bar.timestamp_utc > self._max_seen_timestamp
        ):
            self._max_seen_timestamp = bar.timestamp_utc

    def _release_finalized(self) -> None:
        """Emit every staged minute older than the newest one we have seen.

        Held back entirely while the historical batch is still streaming: an
        older bar may still arrive, and emitting early would drop it.
        """
        if not self._history_complete or self._max_seen_timestamp is None:
            return
        for timestamp in sorted(self._staged):
            if timestamp >= self._max_seen_timestamp:
                break   # the newest timestamp is the still-forming minute
            self._queue.append(self._staged.pop(timestamp))
            self._last_finalized_timestamp = timestamp

    def _complete_history(self) -> None:
        self._history_complete = True
        self._release_finalized()

    def _expire_history_grace(self, now: float) -> None:
        """Never stall forever on a marker that does not arrive."""
        if self._history_complete:
            return
        if self._subscribed_at is None:
            self._subscribed_at = now
            return
        if now - self._subscribed_at >= self._history_grace_seconds:
            self._complete_history()

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
