from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, List, Optional, Tuple

import pytest

from full_python.livedata.contract_authority import ContractAuthority
from full_python.livedata.live_bar_source import ActiveWindow, LiveBarSource
from full_python.tradovate.errors import TradovateFeedError
from full_python.tradovate.feed import TradovateMarketDataFeed, chart_bar_to_vendor_bar


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeMonotonic:
    def __init__(self, values: List[float]) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        if not self.values:
            raise AssertionError("No fake monotonic values left")
        return self.values.pop(0)


class FakeChartWebSocket:
    def __init__(self, events: Optional[List[Optional[dict]]] = None) -> None:
        self.events = list(events or [])
        self.requests: List[Tuple[str, Any]] = []
        self.received_timeouts: List[float] = []

    def request(self, endpoint: str, payload: Any) -> Any:
        self.requests.append((endpoint, payload))
        if endpoint == "md/getChart":
            return {"historicalId": "101", "realtimeId": "202"}
        return {}

    def receive_event(self, timeout_seconds: float) -> Optional[dict]:
        self.received_timeouts.append(timeout_seconds)
        if not self.events:
            return None
        return self.events.pop(0)


def _raw_bar(ts: str, close: float = 100.5) -> dict:
    return {
        "timestamp": ts,
        "open": "100.0",
        "high": "101.0",
        "low": "99.0",
        "close": str(close),
        "upVolume": 7,
        "downVolume": 5,
    }


def _chart_event(chart_id: int, bars: List[dict]) -> dict:
    return {"e": "chart", "d": {"charts": [{"id": chart_id, "bars": bars}]}}


def _eoh_event(chart_id: int = 101) -> dict:
    """Tradovate's end-of-history marker: {id: <historicalId>, eoh: true}.

    Until this arrives the client must gather bars and sort them -- the
    documented protocol makes no ordering guarantee for the historical batch.
    """
    return {"e": "chart", "d": {"charts": [{"id": chart_id, "eoh": True}]}}


class FakeMonotonicClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_chart_bar_to_vendor_bar_normalizes_timestamp_and_volume() -> None:
    raw = _raw_bar("2026-07-07T14:31:00.000Z")

    bar = chart_bar_to_vendor_bar(symbol="NQZ5", raw=raw)

    assert bar.symbol == "NQZ5"
    assert bar.timestamp_utc == "2026-07-07T14:31:00Z"
    assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 101.0, 99.0, 100.5)
    assert bar.volume == 12.0


def test_chart_bar_to_vendor_bar_prefers_explicit_volume() -> None:
    raw = _raw_bar("2026-07-07T14:31Z")
    raw["volume"] = "33"

    bar = chart_bar_to_vendor_bar(symbol="NQZ5", raw=raw)

    assert bar.timestamp_utc == "2026-07-07T14:31:00Z"
    assert bar.volume == 33.0


def test_subscribe_requests_minute_chart_and_stores_subscription_ids() -> None:
    ws = FakeChartWebSocket()
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")

    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=5)

    assert ws.requests == [
        (
            "md/getChart",
            {
                "symbol": "NQZ5",
                "chartDescription": {
                    "underlyingType": "MinuteBar",
                    "elementSize": 1,
                    "elementSizeUnit": "UnderlyingUnits",
                    "withHistogram": False,
                },
                "timeRange": {
                    "closestTimestamp": "2026-07-07T14:31Z",
                    "asMuchAsElements": 5,
                },
            },
        )
    ]
    assert feed.historical_id == 101
    assert feed.realtime_id == 202


def test_next_bar_replaces_forming_bar_and_emits_it_when_next_timestamp_arrives() -> None:
    first = _raw_bar("2026-07-07T14:31:00.000Z", close=100.5)
    duplicate = _raw_bar("2026-07-07T14:31:00Z", close=101.5)
    second = _raw_bar("2026-07-07T14:32:00.000Z", close=102.5)
    ws = FakeChartWebSocket(
        [
            {"e": "props", "d": {"ignored": True}},
            {"e": "chart", "d": {"charts": [{"id": 999, "bars": [first]}]}},
            _chart_event(101, [first, duplicate, second]),
            _eoh_event(),
        ]
    )
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=5)

    finalized = feed.next_bar(timeout_seconds=2.5)
    assert finalized.timestamp_utc == "2026-07-07T14:31:00Z"
    assert finalized.close == 101.5

    # 14:32 is still forming. It must not be emitted until a newer timestamp
    # proves that the minute is complete.
    assert feed.next_bar(timeout_seconds=1.0) is None


def test_historical_batch_emits_every_bar_except_latest_forming_minute() -> None:
    bars = [
        _raw_bar("2026-07-07T14:30:00Z", close=100.0),
        _raw_bar("2026-07-07T14:31:00Z", close=101.0),
        _raw_bar("2026-07-07T14:32:00Z", close=102.0),
    ]
    ws = FakeChartWebSocket([_chart_event(101, bars), _eoh_event()])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:32Z", bars_back=3)

    assert feed.next_bar(1.0).timestamp_utc == "2026-07-07T14:30:00Z"
    assert feed.next_bar(1.0).timestamp_utc == "2026-07-07T14:31:00Z"
    assert feed.next_bar(0.1) is None


def test_later_snapshot_replaces_pending_forming_bar_across_events() -> None:
    ws = FakeChartWebSocket([
        _chart_event(101, [_raw_bar("2026-07-07T14:31:00Z", close=100.5)]),
        _eoh_event(),
        _chart_event(202, [_raw_bar("2026-07-07T14:31:00Z", close=101.5)]),
        _chart_event(202, [_raw_bar("2026-07-07T14:32:00Z", close=102.5)]),
    ])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=1)

    bar = feed.next_bar(2.0)
    assert bar.timestamp_utc == "2026-07-07T14:31:00Z"
    assert bar.close == 101.5


def test_forming_bar_snapshots_are_progress_not_ignorable_noise() -> None:
    # Tradovate updates the forming bar on every tick. The 09:30 open -- the only
    # minute this strategy trades -- is the highest tick-rate minute of the day.
    # Counting each snapshot as an "ignored event" kills the session precisely
    # when it matters most.
    updates = [
        _chart_event(202, [_raw_bar("2026-07-07T14:31:00Z", close=100.0 + i * 0.25)])
        for i in range(300)
    ]
    ws = FakeChartWebSocket([_eoh_event()] + updates
                            + [_chart_event(202, [_raw_bar("2026-07-07T14:32:00Z")])])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5", max_ignored_events=100)
    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=5)

    bar = feed.next_bar(timeout_seconds=5.0)  # must not raise

    assert bar.timestamp_utc == "2026-07-07T14:31:00Z"
    assert bar.close == 100.0 + 299 * 0.25  # the LAST snapshot, not the first


def test_realtime_snapshot_before_history_does_not_discard_warmup() -> None:
    # A single realtime snapshot arriving before the historical batch must not
    # throw away the warmup history: the strategy needs 200 bars before it can
    # trade, and a session without them silently trades nothing.
    history = [_raw_bar(f"2026-07-07T13:{m:02d}:00Z", close=100 + m) for m in range(10, 60)]
    ws = FakeChartWebSocket([
        _chart_event(202, [_raw_bar("2026-07-07T14:31:00Z", close=999.0)]),  # realtime first
        _chart_event(101, history),                                          # history after
        _eoh_event(),
    ])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=400)

    emitted = []
    while True:
        bar = feed.next_bar(timeout_seconds=0.05)
        if bar is None:
            break
        emitted.append(bar.timestamp_utc)

    assert len(emitted) == len(history)          # every warmup bar survives
    assert emitted == sorted(emitted)            # and is emitted in order
    assert emitted[0] == "2026-07-07T13:10:00Z"


def test_out_of_order_history_is_sorted_before_emission() -> None:
    # The protocol makes no ordering guarantee; the client must gather and sort.
    shuffled = [
        _raw_bar("2026-07-07T14:03:00Z"),
        _raw_bar("2026-07-07T14:01:00Z"),
        _raw_bar("2026-07-07T14:02:00Z"),
        _raw_bar("2026-07-07T14:00:00Z"),
    ]
    ws = FakeChartWebSocket([
        _chart_event(101, shuffled[:2]),
        _chart_event(101, shuffled[2:]),
        _eoh_event(),
    ])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:03Z", bars_back=10)

    emitted = []
    while True:
        bar = feed.next_bar(timeout_seconds=0.05)
        if bar is None:
            break
        emitted.append(bar.timestamp_utc)

    assert emitted == [
        "2026-07-07T14:00:00Z",
        "2026-07-07T14:01:00Z",
        "2026-07-07T14:02:00Z",
    ]  # 14:03 is the newest and still forming


def test_history_is_withheld_until_the_end_of_history_marker() -> None:
    # Nothing may be emitted while the historical batch is still streaming: an
    # older bar could still arrive, and emitting early would drop it.
    ws = FakeChartWebSocket([
        _chart_event(101, [_raw_bar("2026-07-07T14:01:00Z"), _raw_bar("2026-07-07T14:02:00Z")]),
    ])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:02Z", bars_back=10)

    assert feed.next_bar(timeout_seconds=0.05) is None
    assert not feed.history_complete


def test_history_grace_timeout_finalizes_when_eoh_never_arrives() -> None:
    # If the marker never comes, the feed must not stall forever.
    clock = FakeMonotonicClock()
    ws = FakeChartWebSocket([
        _chart_event(101, [_raw_bar("2026-07-07T14:01:00Z"), _raw_bar("2026-07-07T14:02:00Z")]),
    ])
    feed = TradovateMarketDataFeed(
        ws, symbol="NQZ5", monotonic_clock=clock, history_grace_seconds=30.0
    )
    feed.subscribe(closest_timestamp="2026-07-07T14:02Z", bars_back=10)

    assert feed.next_bar(timeout_seconds=0.05) is None   # still inside the grace period

    clock.value = 31.0                                    # grace elapsed
    bar = feed.next_bar(timeout_seconds=0.05)

    assert bar is not None
    assert bar.timestamp_utc == "2026-07-07T14:01:00Z"
    assert feed.history_complete


def test_feed_error_is_a_live_data_error_so_the_loop_halts_and_flattens() -> None:
    # TradovateFeedError escaped LiveLoop's halt protocol entirely: it was
    # neither a LiveDataError nor an ExecutionInvariantError, so a feed protocol
    # failure crashed straight through the safety layer.
    from full_python.livedata.errors import LiveDataError

    assert issubclass(TradovateFeedError, LiveDataError)


def test_malformed_bar_inside_a_chart_packet_becomes_a_feed_error() -> None:
    ws = FakeChartWebSocket([
        _chart_event(101, [{"timestamp": "2026-07-07T14:01:00Z", "open": "100.0"}]),
    ])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:01Z", bars_back=5)

    with pytest.raises(TradovateFeedError):
        feed.next_bar(timeout_seconds=0.05)


def test_next_bar_stops_after_too_many_ignored_events() -> None:
    ws = FakeChartWebSocket([{"e": "props", "d": {"ignored": True}}] * 100)
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5", max_ignored_events=100)
    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=5)

    try:
        feed.next_bar(timeout_seconds=1.0)
    except TradovateFeedError as exc:
        assert "Too many Tradovate chart events" in str(exc)
    else:
        raise AssertionError("Expected TradovateFeedError")
    assert len(ws.received_timeouts) == 100


def test_next_bar_uses_remaining_timeout_budget_for_ignored_events() -> None:
    ws = FakeChartWebSocket(
        [
            {"e": "props", "d": {"ignored": True}},
            {"e": "props", "d": {"ignored": True}},
        ]
    )
    clock = FakeMonotonic([10.0, 10.0, 10.25, 11.0])
    feed = TradovateMarketDataFeed(
        ws,
        symbol="NQZ5",
        monotonic_clock=clock,
    )
    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=5)

    assert feed.next_bar(timeout_seconds=1.0) is None
    assert ws.received_timeouts == [1.0, 0.75]


def test_next_bar_matches_realtime_subscription_id() -> None:
    ws = FakeChartWebSocket([
        _eoh_event(),
        _chart_event(202, [
            _raw_bar("2026-07-07T14:33:00Z"),
            _raw_bar("2026-07-07T14:34:00Z"),
        ])
    ])
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=5)

    bar = feed.next_bar(timeout_seconds=1.0)

    assert bar.timestamp_utc == "2026-07-07T14:33:00Z"


def test_cancel_requests_cancel_chart_when_realtime_id_is_known() -> None:
    ws = FakeChartWebSocket()
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")
    feed.subscribe(closest_timestamp="2026-07-07T14:31Z", bars_back=5)

    feed.cancel()

    assert ws.requests[-1] == ("md/cancelChart", {"subscriptionId": 202})


def test_cancel_without_realtime_id_does_not_request_cancel_chart() -> None:
    ws = FakeChartWebSocket()
    feed = TradovateMarketDataFeed(ws, symbol="NQZ5")

    feed.cancel()

    assert ws.requests == []


def test_feed_vendor_bar_is_consumed_by_live_bar_source() -> None:
    auth = ContractAuthority(root="NQ")
    front = auth.front_contract(date(2025, 11, 3))
    ws = FakeChartWebSocket(
        [
            _chart_event(
                101,
                [
                    {
                        "timestamp": "2025-11-03T14:31:00.000Z",
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100.25,
                        "volume": 9,
                    },
                    {
                        "timestamp": "2025-11-03T14:32:00.000Z",
                        "open": 100.25,
                        "high": 101.25,
                        "low": 100,
                        "close": 101,
                        "volume": 8,
                    },
                ],
            ),
            _eoh_event(),
        ]
    )
    feed = TradovateMarketDataFeed(ws, symbol=front)
    feed.subscribe(closest_timestamp="2025-11-03T14:31Z", bars_back=1)
    source = LiveBarSource(
        feed,
        FakeClock(datetime(2025, 11, 3, 14, 31, tzinfo=timezone.utc)),
        auth,
        ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=16 * 60),
        lambda: False,
    )

    bar = next(iter(source))

    assert bar.symbol == front
    assert bar.timestamp_utc == "2025-11-03T14:31:00Z"
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        100.0,
        101.0,
        99.0,
        100.25,
        9.0,
    )


def test_malformed_chart_bar_raises_value_error() -> None:
    import pytest

    bad_bar = {"timestamp": "2025-11-03T14:31:00.000Z", "open": "100.0"}  # missing high/low/close

    with pytest.raises(ValueError, match="Chart bar missing"):
        chart_bar_to_vendor_bar(symbol="NQZ5", raw=bad_bar)
