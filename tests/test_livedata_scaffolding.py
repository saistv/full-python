from datetime import datetime, timezone

import pytest

from full_python.livedata.clock import Clock, SystemClock
from full_python.livedata.errors import (
    DataIntegrityError,
    DataOutageError,
    LiveDataError,
)
from full_python.livedata.feed import MarketDataFeed, VendorBar


def test_vendor_bar_is_frozen_with_expected_fields():
    vb = VendorBar(symbol="NQZ5", timestamp_utc="2025-11-03T14:31:00Z",
                   open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)
    assert vb.symbol == "NQZ5"
    assert vb.timestamp_utc == "2025-11-03T14:31:00Z"
    assert (vb.open, vb.high, vb.low, vb.close, vb.volume) == (1.0, 2.0, 0.5, 1.5, 10.0)
    with pytest.raises(Exception):
        vb.close = 9.0  # frozen


def test_system_clock_now_is_timezone_aware_utc():
    now = SystemClock().now()
    assert isinstance(now, datetime)
    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(None)


def test_error_hierarchy():
    assert issubclass(DataOutageError, LiveDataError)
    assert issubclass(DataIntegrityError, LiveDataError)
    assert issubclass(LiveDataError, RuntimeError)
    assert not issubclass(DataIntegrityError, DataOutageError)


def test_protocols_are_structural():
    # A minimal object satisfying each protocol type-checks structurally.
    class _Feed:
        def next_bar(self, timeout_seconds: float):
            return None
    class _Clk:
        def now(self):
            return datetime.now(timezone.utc)
    assert isinstance(_Feed(), MarketDataFeed)
    assert isinstance(_Clk(), Clock)
