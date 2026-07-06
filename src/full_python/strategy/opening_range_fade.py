"""MR variant 2 -- opening range fade v1.

Fades a FAILED breakout of the 9:30-10:00 ET opening range on
non-trending days. A breakout arms when price extends >= breakout_atr_mult
x ATR(14) beyond the OR edge; it FAILS (and we fade) when a bar closes
back inside the range within failure_window bars. Bracket: 1-ATR frozen
stop, static 2:1 target, 20-bar time stop, ADX(14)<20 gate, 10:00-15:30
entry window (disjoint from Adaptive Trend). Decision-only, like
VwapReversionStrategy: the engine owns fills and the bracket.
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
from full_python.strategy.opening_range_fade_config import OpeningRangeFadeConfig


class OpeningRangeFadeStrategy:
    def __init__(self, config: OpeningRangeFadeConfig) -> None:
        self.config = config
        self._atr = Atr(config.atr_length)
        self._adx = DailyAdx(config.adx_length)
        self._adx_value: Optional[float] = None
        self._bar_index = -1
        self._session_date: Optional[str] = None

        self._session_high = float("-inf")
        self._session_low = float("inf")
        self._session_close = 0.0

        # Opening range for the current session (built over [or_start, or_end)).
        self._or_high: Optional[float] = None
        self._or_low: Optional[float] = None

        # Breakout state, per side.
        self._up_active = False
        self._up_extended = False
        self._up_age = 0
        self._dn_active = False
        self._dn_extended = False
        self._dn_age = 0

        self._position_side: Optional[str] = None
        self._bars_in_trade = 0
        self._bars_since_exit = 999
        self._entry_pending = False
        self._entry_pending_age = 0

    def on_fill(self, fill: Fill) -> None:
        if fill.reason == "opening_range_fade":
            self._position_side = "long" if fill.side == "buy" else "short"
            self._bars_in_trade = 0
            self._entry_pending = False
            self._entry_pending_age = 0

    def on_trade_closed(self, trade: Trade) -> None:
        self._position_side = None
        self._bars_since_exit = 0
        self._entry_pending = False

    def _quantize(self, price: float) -> float:
        tick = self.config.tick_size
        return round(price / tick) * tick

    def _reset_session(self) -> None:
        self._session_high = float("-inf")
        self._session_low = float("inf")
        self._or_high = None
        self._or_low = None
        self._up_active = self._up_extended = False
        self._dn_active = self._dn_extended = False
        self._up_age = self._dn_age = 0
        self._entry_pending = False
        self._entry_pending_age = 0

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        config = self.config
        self._bar_index += 1
        session = classify_timestamp(bar.timestamp_utc)
        session_iso = session.session_date.isoformat()
        minutes = session.minutes_from_midnight_et

        if session_iso != self._session_date:
            if self._session_date is not None and self._session_high > self._session_low:
                self._adx_value = self._adx.update(
                    self._session_high, self._session_low, self._session_close
                )
            self._session_date = session_iso
            self._reset_session()

        self._session_high = max(self._session_high, bar.high)
        self._session_low = min(self._session_low, bar.low)
        self._session_close = bar.close
        atr = self._atr.update(bar.high, bar.low, bar.close)

        # Build the opening range over [or_start, or_end) on RTH bars.
        if session.is_rth and config.or_start_minutes_et <= minutes < config.or_end_minutes_et:
            self._or_high = bar.high if self._or_high is None else max(self._or_high, bar.high)
            self._or_low = bar.low if self._or_low is None else min(self._or_low, bar.low)

        # Position / pending bookkeeping.
        if self._position_side is not None:
            self._bars_in_trade += 1
        else:
            self._bars_since_exit += 1
        if self._entry_pending:
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

        # --- Breakout / failure detection (runs whenever the OR is frozen) ---
        fade_side: Optional[str] = None
        or_ready = (
            self._or_high is not None
            and self._or_low is not None
            and minutes >= config.or_end_minutes_et
            and session.is_rth
            and atr is not None
        )
        if or_ready:
            threshold = config.breakout_atr_mult * atr
            # upside breakout -> fade SHORT on failure
            if self._up_active:
                self._up_age += 1
            if bar.high > self._or_high:
                if not self._up_active:
                    self._up_active = True
                    self._up_age = 0
                    self._up_extended = False
                if (bar.high - self._or_high) >= threshold:
                    self._up_extended = True
            if self._up_active and bar.close < self._or_high:  # closed back inside
                if self._up_extended and self._up_age <= config.failure_window_bars:
                    fade_side = "short"
                self._up_active = self._up_extended = False
                self._up_age = 0
            elif self._up_active and self._up_age > config.failure_window_bars:
                self._up_active = self._up_extended = False
                self._up_age = 0
            # downside breakout -> fade LONG on failure
            if self._dn_active:
                self._dn_age += 1
            if bar.low < self._or_low:
                if not self._dn_active:
                    self._dn_active = True
                    self._dn_age = 0
                    self._dn_extended = False
                if (self._or_low - bar.low) >= threshold:
                    self._dn_extended = True
            if self._dn_active and bar.close > self._or_low:  # closed back inside
                if self._dn_extended and self._dn_age <= config.failure_window_bars and fade_side is None:
                    fade_side = "long"
                self._dn_active = self._dn_extended = False
                self._dn_age = 0
            elif self._dn_active and self._dn_age > config.failure_window_bars:
                self._dn_active = self._dn_extended = False
                self._dn_age = 0

        in_window = (
            config.entry_start_minutes_et <= minutes < config.entry_end_minutes_et
        )
        flat = self._position_side is None and not self._entry_pending
        if not flat or not in_window or self._bar_index < config.warmup_bars or fade_side is None:
            return StrategyResult(exits=exits)

        failing: Optional[str] = None
        if atr is None:
            failing = "indicator_warmup"
        elif self._adx_value is None:
            failing = "adx_warmup"
        elif self._adx_value >= config.adx_max:
            failing = "adx_trending"
        elif self._bars_since_exit < config.cooldown_bars:
            failing = "cooldown"

        if failing is not None:
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side=fade_side,
                    reason=failing,
                ),
                exits=exits,
            )

        stop_distance = config.stop_atr_mult * atr
        if fade_side == "long":
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
            side=fade_side,
            reason="opening_range_fade",
            metadata={
                "stop_price": stop_price,
                "target_price": target_price,
                "or_high": self._or_high,
                "or_low": self._or_low,
                "atr": atr,
                "adx": self._adx_value,
            },
        )
        intent = OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=intent_side,
            quantity=config.contracts,
            reason="opening_range_fade",
            metadata={
                "stop_price": stop_price,
                "target_price": target_price,
                "signal_price": bar.close,
            },
        )
        return StrategyResult(signal=signal, order_intents=(intent,), exits=exits)
