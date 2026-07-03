"""Adaptive Trend — Python port of the validated production signal core.

Ported line-for-line in behavior from the Pine research fork
(``strategy_RESEARCH.pine``, itself the parity twin of the validated
$251K / PF 2.071 / 448-trade production config): pivot S/R breakout with
prove-it confirmation, squeeze momentum (green/red + released state),
wings strong-candle gate, MA50 EMA / MA200 SMA trend filters, ATF trend
alignment, 9:30-10:00 ET entry window, cooldowns 7/1/3, and the Dynamic
S/R stop (buffer 5, floor 15, cap 31, fallback 30) frozen at entry.

Scope (M2, flat parity): fixed 1-contract sizing. Anti-martingale
scaling and the daily-loss-limit guard are deliberately NOT ported yet —
per-trade signal/stop/exit parity against the TradingView export must be
proven before sizing layers stack on top (M2b).

The strategy is decision-only. The simulation engine (or a live adapter)
owns fills, the frozen stop, the 15:59 ET backstop, and session flatten.
Position awareness arrives through the ``on_fill`` / ``on_trade_closed``
feedback hooks; cooldown counters replicate the Pine semantics: they
accrue only while flat, and reset on entry / stop / near-entry exits.

Rejection logging: to keep the ledger useful without drowning it, a
rejected SignalDecision (with the first failing gate as the reason) is
emitted only for in-window, post-warmup, flat bars — the bars on which a
trade was actually possible.
"""
from __future__ import annotations

from collections import deque
import math
from typing import Deque, Optional, Tuple

from full_python.data.sessions import classify_timestamp
from full_python.indicators import (
    AdaptiveTrendFlow,
    Atr,
    Ema,
    PivotHigh,
    PivotLow,
    Sma,
    SqueezeMomentum,
)
from full_python.models import (
    ExitDecision,
    Fill,
    MarketBar,
    OrderIntent,
    SignalDecision,
    StrategyResult,
    Trade,
)
from full_python.strategy.adaptive_trend_config import AdaptiveTrendConfig


class AdaptiveTrendStrategy:
    def __init__(self, config: AdaptiveTrendConfig) -> None:
        self.config = config
        self._atf = AdaptiveTrendFlow(config.atf_length, config.atf_smooth, config.atf_sensitivity)
        self._squeeze = SqueezeMomentum(
            config.sqz_bb_length, config.sqz_bb_mult, config.sqz_kc_length, config.sqz_kc_mult
        )
        self._wings_atr = Atr(config.wings_atr_length)
        self._ma_50 = Ema(config.ma_50_length)
        self._ma_200 = Sma(config.ma_200_length)
        self._pivot_high = PivotHigh(config.sr_left_bars, config.sr_right_bars)
        self._pivot_low = PivotLow(config.sr_left_bars, config.sr_right_bars)

        # recent[0] is the current bar, recent[i] is i bars ago (Pine [i]).
        self._recent: Deque[Tuple[float, Optional[float], Optional[float]]] = deque(
            maxlen=config.sr_break_lookback + 2
        )
        self._bar_index = -1
        self._session_date: Optional[str] = None

        self._prev_resistance_detected = False
        self._prev_support_detected = False
        self._res_hold_count = 0
        self._sup_hold_count = 0
        self._active_res_level: Optional[float] = None
        self._active_sup_level: Optional[float] = None

        self._position_side: Optional[str] = None
        self._entry_just_fired = False
        self._bars_since_last_entry = 999
        self._bars_since_stop_loss = 999
        self._bars_since_breakeven_exit = 999
        self._pending_closed_trade: Optional[Trade] = None
        # M2b sizing state. The win streak persists across sessions (Pine var);
        # session P&L and the halt flag arrive per bar via on_bar_context.
        self._win_streak = 0
        self._session_pnl = 0.0
        self._daily_limit_hit = False

    # ------------------------------------------------------------------
    # Engine feedback hooks
    # ------------------------------------------------------------------

    def on_fill(self, fill: Fill) -> None:
        if fill.reason == "sr_breakout":
            self._position_side = "long" if fill.side == "buy" else "short"

    def on_trade_closed(self, trade: Trade) -> None:
        self._position_side = None
        self._pending_closed_trade = trade
        if self.config.enable_anti_martingale:
            # Pine "Any Non-Win" reset: net (commission-inclusive) P&L must
            # be strictly positive to extend the streak; scratches reset.
            if trade.net_pnl > 0:
                self._win_streak += 1
            else:
                self._win_streak = 0

    def on_bar_context(self, *, session_pnl: float, daily_limit_hit: bool) -> None:
        self._session_pnl = session_pnl
        self._daily_limit_hit = daily_limit_hit

    # ------------------------------------------------------------------
    # Main bar handler
    # ------------------------------------------------------------------

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        config = self.config
        self._bar_index += 1
        session = classify_timestamp(bar.timestamp_utc)
        session_iso = session.session_date.isoformat()
        if session_iso != self._session_date:
            self._session_date = session_iso
            self._reset_break_state()

        atf = self._atf.update(bar.high, bar.low, bar.close)
        squeeze = self._squeeze.update(bar.high, bar.low, bar.close)
        wings_atr = self._wings_atr.update(bar.high, bar.low, bar.close)
        ma_50 = self._ma_50.update(bar.close)
        ma_200 = self._ma_200.update(bar.close)
        pivot_high = self._pivot_high.update(bar.high)
        pivot_low = self._pivot_low.update(bar.low)
        self._recent.appendleft((bar.close, pivot_high, pivot_low))

        resistance_broken, support_broken = self._detect_breaks()
        self._update_prove_it(resistance_broken, support_broken, bar.close, pivot_high, pivot_low)
        resistance_confirmed = (
            config.prove_it_bars
            <= self._res_hold_count
            <= config.prove_it_bars + config.signal_valid_bars
        )
        support_confirmed = (
            config.prove_it_bars
            <= self._sup_hold_count
            <= config.prove_it_bars + config.signal_valid_bars
        )

        self._advance_cooldowns()

        wings_long, wings_short = self._wings(bar, wings_atr)
        in_window = (
            config.entry_start_minutes_et
            <= session.minutes_from_midnight_et
            < config.entry_end_minutes_et
        )
        warmup_complete = self._bar_index >= config.warmup_bars
        cooldown_ok = (
            self._bars_since_last_entry >= config.entry_cooldown_bars
            and self._bars_since_stop_loss >= config.stop_loss_cooldown_bars
            and self._bars_since_breakeven_exit >= config.breakeven_exit_cooldown_bars
        )

        exits: Tuple[ExitDecision, ...] = ()
        if self._position_side == "long" and atf.trend == -1:
            exits = (
                ExitDecision(timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, reason="atf_flip"),
            )
        elif self._position_side == "short" and atf.trend == 1:
            exits = (
                ExitDecision(timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, reason="atf_flip"),
            )

        if not warmup_complete or not in_window or self._position_side is not None:
            return StrategyResult(exits=exits)

        if atf.trend == 1:
            side = "long"
            failing = self._first_failing_long_gate(
                bar, ma_50, ma_200, pivot_high, resistance_confirmed, squeeze, wings_long, cooldown_ok
            )
        elif atf.trend == -1:
            side = "short"
            failing = self._first_failing_short_gate(
                bar, ma_50, ma_200, pivot_low, support_confirmed, squeeze, wings_short, cooldown_ok
            )
        else:
            side = "long"
            failing = "atf_warming_up"

        if failing is None and config.enable_daily_loss_limit and self._daily_limit_hit:
            failing = "daily_limit_halt"

        if failing is not None:
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side=side,
                    reason=failing,
                ),
                exits=exits,
            )

        if side == "long":
            stop_price, capped = self._compute_long_stop(bar.close, pivot_high)
            intent_side = "buy"
            anchor = pivot_high
        else:
            stop_price, capped = self._compute_short_stop(bar.close, pivot_low)
            intent_side = "sell"
            anchor = pivot_low

        if config.enable_anti_martingale:
            quantity_plan = min(
                config.contracts + self._win_streak, config.max_contracts_per_entry
            )
        else:
            quantity_plan = config.contracts
        quantity = self._dll_safe_quantity(bar.close, stop_price, quantity_plan)
        if quantity == 0:
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side=side,
                    reason="dll_projected_risk",
                ),
                exits=exits,
            )

        self._entry_just_fired = True
        self._bars_since_last_entry = 0

        signal = SignalDecision.accepted(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=side,
            reason="sr_breakout",
            metadata={
                "stop_price": stop_price,
                "sr_anchor": anchor,
                "stop_capped": capped,
                "prove_it_hold": self._res_hold_count if side == "long" else self._sup_hold_count,
                "quantity": quantity,
                "quantity_plan": quantity_plan,
                "win_streak": self._win_streak,
            },
        )
        intent = OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=intent_side,
            quantity=quantity,
            reason="sr_breakout",
            metadata={
                "stop_price": stop_price,
                "signal_price": bar.close,
                "stop_capped": capped,
            },
        )
        return StrategyResult(signal=signal, order_intents=(intent,), exits=exits)

    def _dll_safe_quantity(
        self, signal_price: float, stop_price: float, desired_qty: int
    ) -> int:
        """Pine f_projected_dll_safe_qty: fit size to full-stop risk."""
        config = self.config
        if not (config.enable_projected_risk_dll_guard and config.enable_daily_loss_limit):
            return desired_qty
        risk_per_contract = abs(signal_price - stop_price) * config.dollar_point_value
        risk_budget = self._session_pnl + config.daily_loss_limit - config.dll_risk_buffer
        if risk_per_contract > 0 and risk_budget > 0:
            max_safe_qty = int(math.floor((risk_budget - 0.000001) / risk_per_contract))
        else:
            max_safe_qty = 0
        return max(0, min(desired_qty, max_safe_qty))

    # ------------------------------------------------------------------
    # S/R break detection + prove-it (Pine-exact)
    # ------------------------------------------------------------------

    def _detect_breaks(self) -> Tuple[bool, bool]:
        config = self.config
        lookback = config.sr_break_lookback
        resistance_cross = False
        support_cross = False
        if self._bar_index >= lookback and len(self._recent) >= lookback + 1:
            for i in range(lookback):
                close_i, ph_i, pl_i = self._recent[i]
                close_i1, ph_i1, pl_i1 = self._recent[i + 1]
                if ph_i is not None and ph_i1 is not None and close_i > ph_i and close_i1 <= ph_i1:
                    resistance_cross = True
                if pl_i is not None and pl_i1 is not None and close_i < pl_i and close_i1 >= pl_i1:
                    support_cross = True

        resistance_broken = resistance_cross and not self._prev_resistance_detected
        support_broken = support_cross and not self._prev_support_detected
        self._prev_resistance_detected = resistance_cross
        self._prev_support_detected = support_cross
        return resistance_broken, support_broken

    def _update_prove_it(
        self,
        resistance_broken: bool,
        support_broken: bool,
        close: float,
        pivot_high: Optional[float],
        pivot_low: Optional[float],
    ) -> None:
        if resistance_broken:
            self._active_res_level = pivot_high
            self._res_hold_count = 1
        elif self._res_hold_count > 0:
            if self._active_res_level is not None and close > self._active_res_level:
                self._res_hold_count += 1
            else:
                self._res_hold_count = 0
                self._active_res_level = None

        if support_broken:
            self._active_sup_level = pivot_low
            self._sup_hold_count = 1
        elif self._sup_hold_count > 0:
            if self._active_sup_level is not None and close < self._active_sup_level:
                self._sup_hold_count += 1
            else:
                self._sup_hold_count = 0
                self._active_sup_level = None

    def _reset_break_state(self) -> None:
        self._res_hold_count = 0
        self._sup_hold_count = 0
        self._active_res_level = None
        self._active_sup_level = None
        self._prev_resistance_detected = False
        self._prev_support_detected = False

    # ------------------------------------------------------------------
    # Cooldowns (Pine order: accrue while flat, then apply close resets)
    # ------------------------------------------------------------------

    def _advance_cooldowns(self) -> None:
        if self._position_side is None:
            if not self._entry_just_fired:
                self._bars_since_last_entry += 1
                self._bars_since_stop_loss += 1
                self._bars_since_breakeven_exit += 1
            else:
                self._entry_just_fired = False

        trade = self._pending_closed_trade
        if trade is None:
            return
        self._pending_closed_trade = None
        if trade.exit_reason in ("stop", "stop_gap", "daily_limit"):
            self._bars_since_stop_loss = 0
            return
        locked_distance = abs(trade.entry_price - trade.stop_price)
        if locked_distance <= 0:
            locked_distance = self.config.fallback_stop_points
        if abs(trade.exit_price - trade.entry_price) < locked_distance * 0.2:
            self._bars_since_breakeven_exit = 0

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    def _first_failing_long_gate(
        self, bar, ma_50, ma_200, pivot_high, resistance_confirmed, squeeze, wings_long, cooldown_ok
    ) -> Optional[str]:
        if ma_50 is None or ma_200 is None:
            return "ma_warming_up"
        if pivot_high is None:
            return "no_pivot"
        if not (bar.close > ma_50 and bar.close > ma_200):
            return "below_trend_mas"
        if not resistance_confirmed:
            return "sr_not_confirmed"
        if not squeeze.momentum_green:
            return "squeeze_momentum_not_green"
        if not squeeze.released:
            return "squeeze_not_released"
        if not wings_long:
            return "wings_fail"
        if not cooldown_ok:
            return "cooldown"
        return None

    def _first_failing_short_gate(
        self, bar, ma_50, ma_200, pivot_low, support_confirmed, squeeze, wings_short, cooldown_ok
    ) -> Optional[str]:
        if ma_50 is None or ma_200 is None:
            return "ma_warming_up"
        if pivot_low is None:
            return "no_pivot"
        if not (bar.close < ma_50 and bar.close < ma_200):
            return "above_trend_mas"
        if not support_confirmed:
            return "sr_not_confirmed"
        if not squeeze.momentum_red:
            return "squeeze_momentum_not_red"
        if not squeeze.released:
            return "squeeze_not_released"
        if not wings_short:
            return "wings_fail"
        if not cooldown_ok:
            return "cooldown"
        return None

    def _wings(self, bar: MarketBar, wings_atr: Optional[float]) -> Tuple[bool, bool]:
        if wings_atr is None:
            return False, False
        body = abs(bar.close - bar.open)
        candle_range = bar.high - bar.low
        close_position = (bar.close - bar.low) / candle_range if candle_range != 0 else 0.5
        body_ok = body >= wings_atr * self.config.wings_body_atr_frac
        wings_long = body_ok and close_position >= self.config.wings_close_frac and bar.close > bar.open
        wings_short = (
            body_ok
            and close_position <= (1.0 - self.config.wings_close_frac)
            and bar.close < bar.open
        )
        return wings_long, wings_short

    # ------------------------------------------------------------------
    # Dynamic S/R stop (Pine-exact, frozen at entry by the engine)
    # ------------------------------------------------------------------

    def _quantize(self, price: float) -> float:
        tick = self.config.tick_size
        return round(price / tick) * tick

    def _compute_long_stop(self, close: float, pivot_high: Optional[float]) -> Tuple[float, bool]:
        config = self.config
        fallback = min(config.fallback_stop_points, config.max_stop_distance)
        if pivot_high is not None:
            dynamic_stop = pivot_high - config.sr_stop_buffer
            dynamic_distance = close - dynamic_stop
            if dynamic_distance < config.sr_min_stop_distance:
                dynamic_stop = close - config.sr_min_stop_distance
                dynamic_distance = config.sr_min_stop_distance
            if dynamic_stop >= close:
                stop, capped = close - fallback, True
            elif dynamic_distance > config.max_stop_distance:
                stop, capped = close - config.max_stop_distance, True
            else:
                stop, capped = dynamic_stop, False
        else:
            stop, capped = close - fallback, True
        if stop >= close:
            stop, capped = close - fallback, True
        return self._quantize(stop), capped

    def _compute_short_stop(self, close: float, pivot_low: Optional[float]) -> Tuple[float, bool]:
        config = self.config
        fallback = min(config.fallback_stop_points, config.max_stop_distance)
        if pivot_low is not None:
            dynamic_stop = pivot_low + config.sr_stop_buffer
            dynamic_distance = dynamic_stop - close
            if dynamic_distance < config.sr_min_stop_distance:
                dynamic_stop = close + config.sr_min_stop_distance
                dynamic_distance = config.sr_min_stop_distance
            if dynamic_stop <= close:
                stop, capped = close + fallback, True
            elif dynamic_distance > config.max_stop_distance:
                stop, capped = close + config.max_stop_distance, True
            else:
                stop, capped = dynamic_stop, False
        else:
            stop, capped = close + fallback, True
        if stop <= close:
            stop, capped = close + fallback, True
        return self._quantize(stop), capped
