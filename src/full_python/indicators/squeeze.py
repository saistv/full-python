"""Squeeze momentum (LazyBear/TTM style) — streaming Pine port.

Production configuration: BB(20, 2.0), KC(20, 1.5) on True Range.
``released`` is the squeeze-OFF *state* (BB outside KC), matching the
validated production semantics — not the one-bar release event.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from full_python.indicators.streaming import (
    LinregEndpoint,
    PopulationStdev,
    RollingMax,
    RollingMin,
    Sma,
    TrueRange,
)


@dataclass(frozen=True)
class SqueezeState:
    value: Optional[float]
    momentum_green: bool
    momentum_red: bool
    released: bool


class SqueezeMomentum:
    def __init__(
        self,
        bb_length: int = 20,
        bb_mult: float = 2.0,
        kc_length: int = 20,
        kc_mult: float = 1.5,
    ) -> None:
        self._bb_basis = Sma(bb_length)
        self._bb_stdev = PopulationStdev(bb_length)
        self._bb_mult = bb_mult
        self._kc_basis = Sma(kc_length)
        self._true_range = TrueRange()
        self._range_ma = Sma(kc_length)
        self._kc_mult = kc_mult
        self._highest = RollingMax(kc_length)
        self._lowest = RollingMin(kc_length)
        self._close_ma = Sma(kc_length)
        self._linreg = LinregEndpoint(kc_length)
        self._previous_value: Optional[float] = None

    def update(self, high: float, low: float, close: float) -> SqueezeState:
        bb_basis = self._bb_basis.update(close)
        bb_stdev = self._bb_stdev.update(close)
        kc_basis = self._kc_basis.update(close)
        range_ma = self._range_ma.update(self._true_range.update(high, low, close))
        highest = self._highest.update(high)
        lowest = self._lowest.update(low)
        close_ma = self._close_ma.update(close)

        released = False
        if bb_basis is not None and bb_stdev is not None and kc_basis is not None and range_ma is not None:
            bb_dev = self._bb_mult * bb_stdev
            kc_span = range_ma * self._kc_mult
            released = (bb_basis - bb_dev < kc_basis - kc_span) and (
                bb_basis + bb_dev > kc_basis + kc_span
            )

        value: Optional[float] = None
        if highest is not None and lowest is not None and close_ma is not None:
            midline = ((highest + lowest) / 2.0 + close_ma) / 2.0
            value = self._linreg.update(close - midline)

        momentum_green = False
        momentum_red = False
        if value is not None:
            previous = self._previous_value if self._previous_value is not None else 0.0
            momentum_green = value > 0 and value > previous
            momentum_red = value < 0 and value < previous
            self._previous_value = value

        return SqueezeState(
            value=value,
            momentum_green=momentum_green,
            momentum_red=momentum_red,
            released=released,
        )
