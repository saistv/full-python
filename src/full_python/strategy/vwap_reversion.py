"""MR variant 1 — VWAP reversion v0.2-py.

Fades extreme extensions from RTH-anchored session VWAP on non-trending
days. Literature-faithful per the MR research contract: static 2:1 target
anchored at signal, 1-ATR frozen stop, 20-bar time stop, daily ADX(14)<20
regime gate decidable at the open, 2.5-ATR entry band, 10:00-15:30 ET
entry window (deliberately disjoint from Adaptive Trend's 9:30-10:00 so
low correlation is structural, not hoped for).

Decision-only, like AdaptiveTrendStrategy: the engine owns fills, the
frozen stop/target bracket, and the 15:59 backstop. The time stop is a
strategy exit signal (fills next bar open).
"""
from __future__ import annotations

from typing import Optional

from full_python.data.sessions import classify_timestamp
from full_python.indicators import Atr
from full_python.models import (
    ExitDecision,
    Fill,
    MarketBar,
    OrderIntent,
    SignalDecision,
    StrategyResult,
    Trade,
)
from full_python.regime import DailyAdx
from full_python.strategy.vwap_reversion_config import VwapReversionConfig


class VwapReversionStrategy:
    def __init__(self, config: VwapReversionConfig) -> None:
        self.config = config
        self._atr = Atr(config.atr_length)
        self._adx = DailyAdx(config.adx_length)
        self._adx_value: Optional[float] = None
        self._bar_index = -1
        self._session_date: Optional[str] = None

        # Session VWAP state (RTH-anchored) and daily H/L/C for the ADX.
        self._vwap_pv = 0.0
        self._vwap_pv2 = 0.0
        self._vwap_volume = 0.0
        self._session_high = float("-inf")
        self._session_low = float("inf")
        self._session_close = 0.0

        self._position_side: Optional[str] = None
        self._bars_in_trade = 0
        self._bars_since_exit = 999
        self._entry_pending = False
        self._entry_pending_age = 0

    # ------------------------------------------------------------------
    # Engine feedback hooks
    # ------------------------------------------------------------------

    def on_fill(self, fill: Fill) -> None:
        if fill.reason == "vwap_reversion":
            self._position_side = "long" if fill.side == "buy" else "short"
            self._bars_in_trade = 0
            self._entry_pending = False
            self._entry_pending_age = 0

    def on_trade_closed(self, trade: Trade) -> None:
        self._position_side = None
        self._bars_since_exit = 0
        self._entry_pending = False

    # ------------------------------------------------------------------

    def _quantize(self, price: float) -> float:
        tick = self.config.tick_size
        return round(price / tick) * tick

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        config = self.config
        self._bar_index += 1
        session = classify_timestamp(bar.timestamp_utc)
        session_iso = session.session_date.isoformat()
        if session_iso != self._session_date:
            # Fold the completed session into the daily ADX, then reset.
            if self._session_date is not None and self._session_high > self._session_low:
                self._adx_value = self._adx.update(
                    self._session_high, self._session_low, self._session_close
                )
            self._session_date = session_iso
            self._session_high = float("-inf")
            self._session_low = float("inf")
            self._vwap_pv = 0.0
            self._vwap_pv2 = 0.0
            self._vwap_volume = 0.0
            self._entry_pending = False
            self._entry_pending_age = 0

        self._session_high = max(self._session_high, bar.high)
        self._session_low = min(self._session_low, bar.low)
        self._session_close = bar.close

        atr = self._atr.update(bar.high, bar.low, bar.close)

        vwap: Optional[float] = None
        vwap_sigma: Optional[float] = None
        if session.is_rth:
            typical = (bar.high + bar.low + bar.close) / 3.0
            volume = max(bar.volume, 0.0)
            self._vwap_pv += typical * volume
            self._vwap_pv2 += typical * typical * volume
            self._vwap_volume += volume
            if self._vwap_volume > 0:
                vwap = self._vwap_pv / self._vwap_volume
                variance = self._vwap_pv2 / self._vwap_volume - vwap * vwap
                vwap_sigma = variance ** 0.5 if variance > 0 else None

        if self._position_side is not None:
            self._bars_in_trade += 1
        else:
            self._bars_since_exit += 1
        if self._entry_pending:
            # Fills arrive one bar after the intent; anything older means the
            # order was vetoed or cancelled (session end) and never filled.
            self._entry_pending_age += 1
            if self._entry_pending_age > 2:
                self._entry_pending = False
                self._entry_pending_age = 0

        exits: tuple[ExitDecision, ...] = ()
        if self._position_side is not None and self._bars_in_trade >= config.time_stop_bars:
            exits = (
                ExitDecision(
                    timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, reason="time_stop"
                ),
            )

        in_window = (
            config.entry_start_minutes_et
            <= session.minutes_from_midnight_et
            < config.entry_end_minutes_et
        )
        flat = self._position_side is None and not self._entry_pending
        if not flat or not in_window or self._bar_index < config.warmup_bars:
            return StrategyResult(exits=exits)

        sigma_unit = vwap_sigma if config.band_mode == "vwap_sigma" else atr
        failing: Optional[str] = None
        side = "long"
        if atr is None or vwap is None or sigma_unit is None:
            failing = "indicator_warmup"
        elif self._adx_value is None:
            failing = "adx_warmup"
        elif self._adx_value >= config.adx_max:
            failing = "adx_trending"
        else:
            band = config.band_atr_mult * sigma_unit
            if bar.close > vwap + band:
                side = "short"
            elif bar.close < vwap - band:
                side = "long"
            else:
                failing = "no_extension"
        if failing is None and self._bars_since_exit < config.cooldown_bars:
            failing = "cooldown"

        if failing is not None:
            # Only log the informative rejections; "no_extension" would be
            # nearly every bar and drown the ledger.
            if failing != "no_extension":
                return StrategyResult(
                    signal=SignalDecision.rejected(
                        timestamp_utc=bar.timestamp_utc,
                        symbol=bar.symbol,
                        side=side,
                        reason=failing,
                    ),
                    exits=exits,
                )
            return StrategyResult(exits=exits)

        stop_distance = config.stop_atr_mult * atr
        if side == "long":
            stop_price = self._quantize(bar.close - stop_distance)
            target_price = self._quantize(bar.close + config.rr_multiple * stop_distance)
            intent_side = "buy"
        else:
            stop_price = self._quantize(bar.close + stop_distance)
            target_price = self._quantize(bar.close - config.rr_multiple * stop_distance)
            intent_side = "sell"

        self._entry_pending = True
        self._entry_pending_age = 0
        signal = SignalDecision.accepted(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=side,
            reason="vwap_reversion",
            metadata={
                "stop_price": stop_price,
                "target_price": target_price,
                "vwap": vwap,
                "atr": atr,
                "adx": self._adx_value,
                "extension_atr": abs(bar.close - vwap) / sigma_unit if sigma_unit else None,
            },
        )
        intent = OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=intent_side,
            quantity=config.contracts,
            reason="vwap_reversion",
            metadata={
                "stop_price": stop_price,
                "target_price": target_price,
                "signal_price": bar.close,
            },
        )
        return StrategyResult(signal=signal, order_intents=(intent,), exits=exits)
