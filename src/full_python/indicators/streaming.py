"""Streaming indicator primitives matching TradingView Pine semantics.

Every class consumes one bar (or value) at a time and returns the current
indicator value, or ``None`` while warming up. Streaming (rather than
vectorized) computation is deliberate: the replay engine, and later live
shadow mode, both process bars one at a time, so research and live paths
share the exact same indicator code.

Pine-semantics notes (validated against the legacy research library):
- ``Ema``: alpha = 2/(len+1), seeded with the first value.
- ``PopulationStdev``: ddof=0, matching ``ta.stdev``.
- ``Rma``: Wilder smoothing seeded with the SMA of the first ``length``
  values, matching ``ta.rma`` (and therefore ``ta.atr``).
- ``TrueRange``: first bar uses high-low (Pine's ta.tr with na handling).
- ``LinregEndpoint``: ``ta.linreg(src, len, 0)`` — regression value at the
  current bar.
- ``PivotHigh``/``PivotLow``: strict pivots confirmed ``right`` bars late;
  ``shifted_held`` reproduces ``fixnan(ta.pivothigh(l, r)[1])`` exactly
  (shift-then-ffill equals ffill-then-shift for this construction).
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional


class Ema:
    def __init__(self, length: int) -> None:
        if length < 1:
            raise ValueError("length must be >= 1")
        self._alpha = 2.0 / (length + 1)
        self._value: Optional[float] = None

    def update(self, value: float) -> float:
        if self._value is None:
            self._value = value
        else:
            self._value = self._alpha * value + (1 - self._alpha) * self._value
        return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value


class Sma:
    def __init__(self, length: int) -> None:
        if length < 1:
            raise ValueError("length must be >= 1")
        self._length = length
        self._window: Deque[float] = deque(maxlen=length)
        self._sum = 0.0

    def update(self, value: float) -> Optional[float]:
        if len(self._window) == self._length:
            self._sum -= self._window[0]
        self._window.append(value)
        self._sum += value
        if len(self._window) < self._length:
            return None
        return self._sum / self._length

    @property
    def value(self) -> Optional[float]:
        if len(self._window) < self._length:
            return None
        return self._sum / self._length


class PopulationStdev:
    """Rolling population standard deviation (ddof=0), like ta.stdev."""

    def __init__(self, length: int) -> None:
        if length < 2:
            raise ValueError("length must be >= 2")
        self._length = length
        self._window: Deque[float] = deque(maxlen=length)

    def update(self, value: float) -> Optional[float]:
        self._window.append(value)
        if len(self._window) < self._length:
            return None
        mean = sum(self._window) / self._length
        variance = sum((item - mean) ** 2 for item in self._window) / self._length
        return variance ** 0.5


class Rma:
    """Wilder smoothing seeded with the SMA of the first ``length`` values."""

    def __init__(self, length: int) -> None:
        if length < 1:
            raise ValueError("length must be >= 1")
        self._length = length
        self._alpha = 1.0 / length
        self._seed_values: list[float] = []
        self._value: Optional[float] = None

    def update(self, value: float) -> Optional[float]:
        if self._value is None:
            self._seed_values.append(value)
            if len(self._seed_values) < self._length:
                return None
            self._value = sum(self._seed_values) / self._length
            return self._value
        self._value = self._alpha * value + (1 - self._alpha) * self._value
        return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value


class TrueRange:
    def __init__(self) -> None:
        self._previous_close: Optional[float] = None

    def update(self, high: float, low: float, close: float) -> float:
        if self._previous_close is None:
            result = high - low
        else:
            result = max(
                high - low,
                abs(high - self._previous_close),
                abs(low - self._previous_close),
            )
        self._previous_close = close
        return result


class Atr:
    def __init__(self, length: int) -> None:
        self._true_range = TrueRange()
        self._rma = Rma(length)

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        return self._rma.update(self._true_range.update(high, low, close))

    @property
    def value(self) -> Optional[float]:
        return self._rma.value


class RollingMax:
    def __init__(self, length: int) -> None:
        self._length = length
        self._window: Deque[float] = deque(maxlen=length)

    def update(self, value: float) -> Optional[float]:
        self._window.append(value)
        if len(self._window) < self._length:
            return None
        return max(self._window)


class RollingMin:
    def __init__(self, length: int) -> None:
        self._length = length
        self._window: Deque[float] = deque(maxlen=length)

    def update(self, value: float) -> Optional[float]:
        self._window.append(value)
        if len(self._window) < self._length:
            return None
        return min(self._window)


class LinregEndpoint:
    """ta.linreg(src, length, 0): regression line value at the current bar."""

    def __init__(self, length: int) -> None:
        if length < 2:
            raise ValueError("length must be >= 2")
        self._length = length
        self._window: Deque[float] = deque(maxlen=length)
        n = float(length)
        self._sum_x = n * (n - 1) / 2.0
        self._sum_x2 = n * (n - 1) * (2 * n - 1) / 6.0
        self._denom = n * self._sum_x2 - self._sum_x ** 2

    def update(self, value: float) -> Optional[float]:
        self._window.append(value)
        if len(self._window) < self._length:
            return None
        n = float(self._length)
        sum_y = sum(self._window)
        sum_xy = sum(index * item for index, item in enumerate(self._window))
        slope = (n * sum_xy - self._sum_x * sum_y) / self._denom
        intercept = (sum_y - slope * self._sum_x) / n
        return slope * (n - 1) + intercept


class _PivotBase:
    """Shared machinery for strict pivots with Pine's shift-and-fixnan view.

    ``update`` returns the value of ``fixnan(ta.pivot*(left, right)[1])`` for
    the bar just fed in: the held pivot as of the *previous* bar.
    """

    def __init__(self, left: int, right: int) -> None:
        if left < 1 or right < 1:
            raise ValueError("left and right must be >= 1")
        self._left = left
        self._right = right
        self._window: Deque[float] = deque(maxlen=left + right + 1)
        self._held: Optional[float] = None

    def _is_pivot(self, center: float, before, after) -> bool:
        raise NotImplementedError

    def update(self, value: float) -> Optional[float]:
        shifted = self._held
        self._window.append(value)
        if len(self._window) == self._left + self._right + 1:
            items = list(self._window)
            center = items[self._left]
            if self._is_pivot(center, items[: self._left], items[self._left + 1 :]):
                self._held = center
        return shifted


class PivotHigh(_PivotBase):
    """Non-strict left, strict right, matching Pine ta.pivothigh on ties:
    when equal extremes tie, the LATER bar of the tie is the pivot.

    Empirically verified against TradingView on two independent tie dates
    (NQ 1m 2026-01-19 and 2026-05-13): this rule reconciles 120/120 trades
    exactly; strict-both-sides and non-strict-right each miss one date.
    """

    def _is_pivot(self, center: float, before, after) -> bool:
        return all(item <= center for item in before) and all(
            item < center for item in after
        )


class PivotLow(_PivotBase):
    """Non-strict left, strict right (see PivotHigh for the verification)."""

    def _is_pivot(self, center: float, before, after) -> bool:
        return all(item >= center for item in before) and all(
            item > center for item in after
        )
