"""Adaptive Trend Flow (ATF) — streaming port of the validated Pine core.

basis = (EMA(hlc3, len) + EMA(hlc3, 2*len)) / 2
bands = basis +/- EMA(stdev(hlc3, len), smooth) * sensitivity
trend state machine: long until close < lower band, short until close >
upper band. Trend is 0 (undefined) until the volatility chain has warmed
up, matching Pine's behavior where na bands keep re-initializing the state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from full_python.indicators.streaming import Ema, PopulationStdev


@dataclass(frozen=True)
class AtfState:
    trend: int  # 1 long, -1 short, 0 warming up
    basis: Optional[float]
    upper: Optional[float]
    lower: Optional[float]


class AdaptiveTrendFlow:
    def __init__(self, length: int = 12, smooth: int = 22, sensitivity: float = 4.5) -> None:
        self._fast = Ema(length)
        self._slow = Ema(length * 2)
        self._stdev = PopulationStdev(length)
        self._smooth_vol = Ema(smooth)
        self._sensitivity = sensitivity
        self._trend = 0

    def update(self, high: float, low: float, close: float) -> AtfState:
        typical = (high + low + close) / 3.0
        fast = self._fast.update(typical)
        slow = self._slow.update(typical)
        basis = (fast + slow) / 2.0

        vol = self._stdev.update(typical)
        if vol is None:
            return AtfState(trend=0, basis=basis, upper=None, lower=None)
        smooth_vol = self._smooth_vol.update(vol)
        upper = basis + smooth_vol * self._sensitivity
        lower = basis - smooth_vol * self._sensitivity

        if self._trend == 0:
            self._trend = 1 if close > basis else -1
        elif self._trend == 1:
            if close < lower:
                self._trend = -1
        else:
            if close > upper:
                self._trend = 1

        return AtfState(trend=self._trend, basis=basis, upper=upper, lower=lower)
