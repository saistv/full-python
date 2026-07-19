"""Opening Auction Level-Retest v2.

The first 15 completed RTH minutes classify acceptance or rejection of an
overnight/prior-RTH extreme.  Classification is context only.  A trade requires
the first post-opening retest to hold and a later completed bar to confirm.  The
module owns its state and types so the rejected v1 artifact remains immutable.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
import math
import statistics
from typing import Any, Deque, Optional

from full_python.data.databento import front_contract_for_session
from full_python.data.sessions import classify_timestamp
from full_python.models import (
    ExitDecision,
    Fill,
    MarketBar,
    OrderIntent,
    SignalDecision,
    StrategyResult,
    Trade,
)
from full_python.strategy.opening_auction_retest_config import (
    OpeningAuctionRetestConfig,
)


ACCEPTED_BREAK_REASON = "opening_auction_accepted_break_retest"
REJECTED_BREAK_REASON = "opening_auction_rejected_break_retest"


class RetestRegime(str, Enum):
    ACCEPTED_BREAK = "accepted_break"
    REJECTED_BREAK = "rejected_break"
    NO_TRADE = "no_trade"


class RetestSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    NONE = "none"


class RetestState(str, Enum):
    OBSERVE = "observe"
    WAIT_FIRST_RETEST = "wait_first_retest"
    ARMED = "armed"
    ENTRY_PENDING = "entry_pending"
    POSITION = "position"
    DONE = "done"


@dataclass(frozen=True)
class RetestFeatures:
    session_date: str
    classification_timestamp_utc: str
    complete_observation: bool
    roll_transition: bool
    complete_overnight: bool
    overnight_bar_count: int
    overnight_max_gap_minutes: Optional[int]
    opening_minutes: tuple[int, ...]
    opening_closes: tuple[float, ...]
    dtr20: Optional[float]
    opening_volume_ratio: Optional[float]
    rth_open: Optional[float]
    opening_high: Optional[float]
    opening_low: Optional[float]
    opening_close: Optional[float]
    opening_width: Optional[float]
    opening_midpoint: Optional[float]
    displacement_dtr: Optional[float]
    efficiency_ratio: Optional[float]
    close_location: Optional[float]
    opening_vwap: Optional[float]
    closes_above_vwap: int
    closes_below_vwap: int
    overnight_high: Optional[float]
    overnight_low: Optional[float]
    prior_rth_high: Optional[float]
    prior_rth_low: Optional[float]
    prior_rth_close: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetestClassification:
    regime: RetestRegime
    side: RetestSide
    reason: str
    reference_side: Optional[str] = None
    reference_type: Optional[str] = None
    reference_price: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "side": self.side.value,
            "reason": self.reason,
            "reference_side": self.reference_side,
            "reference_type": self.reference_type,
            "reference_price": self.reference_price,
        }


@dataclass(frozen=True)
class RetestSessionSnapshot:
    features: RetestFeatures
    classification: RetestClassification

    def to_dict(self) -> dict[str, Any]:
        return {**self.features.to_dict(), **self.classification.to_dict()}


@dataclass(frozen=True)
class RetestDiagnosticEvent:
    session_date: str
    timestamp_utc: str
    event: str
    regime: str
    side: str
    state: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _chosen_crossed_reference(
    features: RetestFeatures,
    config: OpeningAuctionRetestConfig,
    *,
    reference_side: str,
) -> Optional[tuple[str, float]]:
    """Choose the outermost external level actually crossed by the opening."""
    assert features.dtr20 is not None
    assert features.rth_open is not None
    assert features.opening_high is not None
    assert features.opening_low is not None
    assert features.overnight_high is not None
    assert features.overnight_low is not None
    assert features.prior_rth_high is not None
    assert features.prior_rth_low is not None

    dtr = features.dtr20
    cross = config.reference_cross_dtr * dtr
    if reference_side == "high":
        references = (
            ("overnight_high", features.overnight_high),
            ("prior_rth_high", features.prior_rth_high),
        )
        crossed = [
            (name, float(level))
            for name, level in references
            if float(level) > features.rth_open
            and features.opening_high >= float(level) + cross
        ]
        if not crossed:
            return None
        price = max(level for _, level in crossed)
    elif reference_side == "low":
        references = (
            ("overnight_low", features.overnight_low),
            ("prior_rth_low", features.prior_rth_low),
        )
        crossed = [
            (name, float(level))
            for name, level in references
            if float(level) < features.rth_open
            and features.opening_low <= float(level) - cross
        ]
        if not crossed:
            return None
        price = min(level for _, level in crossed)
    else:
        raise ValueError(f"unknown reference side: {reference_side}")

    labels = [name for name, level in crossed if level == price]
    label = labels[0] if len(labels) == 1 else f"overnight_and_prior_rth_{reference_side}"
    return label, price


def classify_opening_auction_retest(
    features: RetestFeatures,
    config: OpeningAuctionRetestConfig,
) -> RetestClassification:
    """Pure, mirrored classifier using only the frozen opening observation."""
    if not features.complete_observation:
        return RetestClassification(
            RetestRegime.NO_TRADE, RetestSide.NONE, "incomplete_opening_observation"
        )
    if features.roll_transition:
        return RetestClassification(
            RetestRegime.NO_TRADE, RetestSide.NONE, "continuous_contract_roll"
        )
    if not features.complete_overnight:
        return RetestClassification(
            RetestRegime.NO_TRADE, RetestSide.NONE, "incomplete_overnight_coverage"
        )

    required = (
        features.dtr20,
        features.rth_open,
        features.opening_high,
        features.opening_low,
        features.opening_close,
        features.opening_vwap,
        features.overnight_high,
        features.overnight_low,
        features.prior_rth_high,
        features.prior_rth_low,
        features.prior_rth_close,
    )
    if any(value is None or not math.isfinite(float(value)) for value in required):
        return RetestClassification(
            RetestRegime.NO_TRADE, RetestSide.NONE, "missing_reference_history"
        )
    if len(features.opening_closes) != config.observation_minutes:
        return RetestClassification(
            RetestRegime.NO_TRADE, RetestSide.NONE, "incomplete_opening_closes"
        )
    if any(not math.isfinite(float(value)) for value in features.opening_closes):
        return RetestClassification(
            RetestRegime.NO_TRADE, RetestSide.NONE, "nonfinite_opening_closes"
        )
    dtr = float(features.dtr20)
    if dtr <= 0:
        return RetestClassification(
            RetestRegime.NO_TRADE, RetestSide.NONE, "invalid_daily_range_scale"
        )

    high_reference = _chosen_crossed_reference(
        features, config, reference_side="high"
    )
    low_reference = _chosen_crossed_reference(
        features, config, reference_side="low"
    )
    close = float(features.opening_close)
    vwap = float(features.opening_vwap)
    acceptance_margin = config.acceptance_margin_dtr * dtr
    rejection_margin = config.rejection_margin_dtr * dtr
    acceptance_closes = features.opening_closes[-config.acceptance_lookback_bars :]
    rejection_closes = features.opening_closes[-config.rejection_lookback_bars :]

    candidates: list[RetestClassification] = []
    if high_reference is not None:
        label, reference = high_reference
        if (
            close >= reference + acceptance_margin
            and sum(item > reference for item in acceptance_closes)
            >= config.acceptance_closes_required
            and vwap > reference
        ):
            candidates.append(
                RetestClassification(
                    RetestRegime.ACCEPTED_BREAK,
                    RetestSide.LONG,
                    "external_high_accepted",
                    "high",
                    label,
                    reference,
                )
            )
        if (
            close <= reference - rejection_margin
            and all(item < reference for item in rejection_closes)
            and vwap < reference
        ):
            candidates.append(
                RetestClassification(
                    RetestRegime.REJECTED_BREAK,
                    RetestSide.SHORT,
                    "external_high_rejected",
                    "high",
                    label,
                    reference,
                )
            )

    if low_reference is not None:
        label, reference = low_reference
        if (
            close <= reference - acceptance_margin
            and sum(item < reference for item in acceptance_closes)
            >= config.acceptance_closes_required
            and vwap < reference
        ):
            candidates.append(
                RetestClassification(
                    RetestRegime.ACCEPTED_BREAK,
                    RetestSide.SHORT,
                    "external_low_accepted",
                    "low",
                    label,
                    reference,
                )
            )
        if (
            close >= reference + rejection_margin
            and all(item > reference for item in rejection_closes)
            and vwap > reference
        ):
            candidates.append(
                RetestClassification(
                    RetestRegime.REJECTED_BREAK,
                    RetestSide.LONG,
                    "external_low_rejected",
                    "low",
                    label,
                    reference,
                )
            )

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return RetestClassification(
            RetestRegime.NO_TRADE, RetestSide.NONE, "conflicting_auction_evidence"
        )
    return RetestClassification(
        RetestRegime.NO_TRADE, RetestSide.NONE, "no_accepted_or_rejected_level"
    )


class OpeningAuctionRetestStrategy:
    def __init__(self, config: OpeningAuctionRetestConfig) -> None:
        self.config = config
        self._dtr_history: Deque[float] = deque(
            maxlen=config.daily_range_lookback_sessions
        )
        self._opening_volume_history: Deque[float] = deque(
            maxlen=config.opening_volume_lookback_sessions
        )
        self._session_date: Optional[str] = None
        self._current_contract: Optional[str] = None
        self._previous_rth_contract: Optional[str] = None
        self._prior_rth_high: Optional[float] = None
        self._prior_rth_low: Optional[float] = None
        self._prior_rth_close: Optional[float] = None
        self._session_snapshots: list[RetestSessionSnapshot] = []
        self._diagnostic_events: list[RetestDiagnosticEvent] = []
        self._position_side: Optional[str] = None
        self._position_branch: Optional[str] = None
        self._entry_pending = False
        self._entry_pending_age = 0
        self._exit_pending = False
        self._reset_session_state()

    @property
    def session_diagnostics(self) -> tuple[RetestSessionSnapshot, ...]:
        return tuple(self._session_snapshots)

    @property
    def diagnostic_events(self) -> tuple[RetestDiagnosticEvent, ...]:
        return tuple(self._diagnostic_events)

    def on_fill(self, fill: Fill) -> None:
        if fill.reason not in (ACCEPTED_BREAK_REASON, REJECTED_BREAK_REASON):
            return
        self._position_side = (
            RetestSide.LONG.value if fill.side == "buy" else RetestSide.SHORT.value
        )
        self._position_branch = (
            RetestRegime.ACCEPTED_BREAK.value
            if fill.reason == ACCEPTED_BREAK_REASON
            else RetestRegime.REJECTED_BREAK.value
        )
        self._entry_pending = False
        self._entry_pending_age = 0
        self._entry_state = RetestState.POSITION
        self._event(
            fill.timestamp_utc,
            "filled",
            metadata={"price": fill.price, "quantity": fill.quantity, "reason": fill.reason},
        )

    def on_trade_closed(self, trade: Trade) -> None:
        self._event(
            trade.exit_timestamp_utc,
            "trade_closed",
            metadata={
                "net_pnl": trade.net_pnl,
                "exit_reason": trade.exit_reason,
                "mfe_points": trade.mfe_points,
                "mae_points": trade.mae_points,
            },
        )
        self._position_side = None
        self._position_branch = None
        self._entry_pending = False
        self._entry_pending_age = 0
        self._exit_pending = False
        self._entry_state = RetestState.DONE

    def _event(
        self,
        timestamp_utc: str,
        event: str,
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        classification = self._classification
        self._diagnostic_events.append(
            RetestDiagnosticEvent(
                session_date=self._session_date or "unknown",
                timestamp_utc=timestamp_utc,
                event=event,
                regime=(
                    classification.regime.value
                    if classification is not None
                    else RetestRegime.NO_TRADE.value
                ),
                side=(
                    classification.side.value
                    if classification is not None
                    else RetestSide.NONE.value
                ),
                state=self._entry_state.value,
                metadata={} if metadata is None else dict(metadata),
            )
        )

    def _reset_session_state(self) -> None:
        self._roll_transition = False
        self._overnight_high: Optional[float] = None
        self._overnight_low: Optional[float] = None
        self._overnight_offsets: list[int] = []
        self._rth_started = False
        self._rth_high: Optional[float] = None
        self._rth_low: Optional[float] = None
        self._rth_close: Optional[float] = None
        self._last_session_timestamp: Optional[str] = None
        self._vwap_pv = 0.0
        self._vwap_volume = 0.0
        self._current_vwap: Optional[float] = None
        self._opening_minutes: list[int] = []
        self._opening_minute_set: set[int] = set()
        self._opening_closes: list[float] = []
        self._opening_vwap_sides: list[str] = []
        self._opening_volume = 0.0
        self._rth_open: Optional[float] = None
        self._opening_high: Optional[float] = None
        self._opening_low: Optional[float] = None
        self._opening_close: Optional[float] = None
        self._classification: Optional[RetestClassification] = None
        self._current_features: Optional[RetestFeatures] = None
        self._entry_state = RetestState.OBSERVE
        self._entry_attempted = False
        self._retest_high: Optional[float] = None
        self._retest_low: Optional[float] = None
        self._structural_extreme: Optional[float] = None
        self._confirmation_bars_seen = 0

    def _finalize_session(self) -> None:
        if self._session_date is None:
            return
        if self._rth_started and self._classification is None:
            timestamp = self._last_session_timestamp or f"{self._session_date}T00:00:00Z"
            self._freeze_classification(timestamp)

        expected = tuple(
            range(
                self.config.observation_start_minutes_et,
                self.config.observation_end_minutes_et,
            )
        )
        if tuple(self._opening_minutes) == expected and self._opening_volume > 0:
            self._opening_volume_history.append(self._opening_volume)

        if (
            self._rth_high is not None
            and self._rth_low is not None
            and self._rth_close is not None
        ):
            if self._prior_rth_close is not None and not self._roll_transition:
                true_range = max(
                    self._rth_high - self._rth_low,
                    abs(self._rth_high - self._prior_rth_close),
                    abs(self._rth_low - self._prior_rth_close),
                )
                if true_range > 0 and math.isfinite(true_range):
                    self._dtr_history.append(true_range)
            self._prior_rth_high = self._rth_high
            self._prior_rth_low = self._rth_low
            self._prior_rth_close = self._rth_close
            self._previous_rth_contract = self._current_contract

    def _start_session(self, session_date: date) -> None:
        self._finalize_session()
        self._session_date = session_date.isoformat()
        self._current_contract = front_contract_for_session(
            session_date, root=self.config.contract_root
        )
        previous_contract = self._previous_rth_contract
        self._reset_session_state()
        self._roll_transition = (
            previous_contract is not None and previous_contract != self._current_contract
        )
        self._entry_pending = False
        self._entry_pending_age = 0
        self._exit_pending = False

    def _update_vwap(self, bar: MarketBar) -> None:
        volume = max(0.0, bar.volume)
        typical = (bar.high + bar.low + bar.close) / 3.0
        self._vwap_pv += typical * volume
        self._vwap_volume += volume
        if self._vwap_volume > 0:
            self._current_vwap = self._vwap_pv / self._vwap_volume

    def _record_opening_bar(self, bar: MarketBar, minute: int) -> None:
        if minute in self._opening_minute_set:
            return
        self._opening_minute_set.add(minute)
        self._opening_minutes.append(minute)
        if minute == self.config.observation_start_minutes_et:
            self._rth_open = bar.open
        self._opening_high = (
            bar.high if self._opening_high is None else max(self._opening_high, bar.high)
        )
        self._opening_low = (
            bar.low if self._opening_low is None else min(self._opening_low, bar.low)
        )
        self._opening_close = bar.close
        self._opening_closes.append(bar.close)
        self._opening_volume += max(0.0, bar.volume)
        if self._current_vwap is None or bar.close == self._current_vwap:
            side = "flat"
        elif bar.close > self._current_vwap:
            side = RetestSide.LONG.value
        else:
            side = RetestSide.SHORT.value
        self._opening_vwap_sides.append(side)

    def _build_features(self, timestamp_utc: str) -> RetestFeatures:
        expected = tuple(
            range(
                self.config.observation_start_minutes_et,
                self.config.observation_end_minutes_et,
            )
        )
        complete = tuple(self._opening_minutes) == expected
        width: Optional[float] = None
        midpoint: Optional[float] = None
        close_location: Optional[float] = None
        efficiency: Optional[float] = None
        displacement_dtr: Optional[float] = None
        if self._opening_high is not None and self._opening_low is not None:
            width = self._opening_high - self._opening_low
            midpoint = (self._opening_high + self._opening_low) / 2.0
        if width is not None and width > 0 and self._opening_close is not None:
            close_location = (self._opening_close - float(self._opening_low)) / width
        if self._rth_open is not None and self._opening_close is not None:
            previous = self._rth_open
            path = 0.0
            for close in self._opening_closes:
                path += abs(close - previous)
                previous = close
            if path > 0:
                efficiency = abs(self._opening_close - self._rth_open) / path

        dtr20 = (
            statistics.median(self._dtr_history)
            if len(self._dtr_history) == self.config.daily_range_lookback_sessions
            else None
        )
        if dtr20 and self._rth_open is not None and self._opening_close is not None:
            displacement_dtr = (self._opening_close - self._rth_open) / dtr20
        volume_ratio: Optional[float] = None
        if len(self._opening_volume_history) == self.config.opening_volume_lookback_sessions:
            typical_volume = statistics.median(self._opening_volume_history)
            if typical_volume > 0:
                volume_ratio = self._opening_volume / typical_volume

        overnight_max_gap: Optional[int] = None
        if len(self._overnight_offsets) >= 2:
            overnight_max_gap = max(
                current - previous
                for previous, current in zip(
                    self._overnight_offsets, self._overnight_offsets[1:]
                )
            )
        complete_overnight = bool(
            self._overnight_offsets
            and self._overnight_offsets[0]
            <= self.config.overnight_start_tolerance_minutes
            and self._overnight_offsets[-1]
            >= 929 - self.config.overnight_preopen_tolerance_minutes
            and overnight_max_gap is not None
            and overnight_max_gap <= self.config.overnight_max_gap_minutes
        )

        return RetestFeatures(
            session_date=self._session_date or "unknown",
            classification_timestamp_utc=timestamp_utc,
            complete_observation=complete,
            roll_transition=self._roll_transition,
            complete_overnight=complete_overnight,
            overnight_bar_count=len(self._overnight_offsets),
            overnight_max_gap_minutes=overnight_max_gap,
            opening_minutes=tuple(self._opening_minutes),
            opening_closes=tuple(self._opening_closes),
            dtr20=dtr20,
            opening_volume_ratio=volume_ratio,
            rth_open=self._rth_open,
            opening_high=self._opening_high,
            opening_low=self._opening_low,
            opening_close=self._opening_close,
            opening_width=width,
            opening_midpoint=midpoint,
            displacement_dtr=displacement_dtr,
            efficiency_ratio=efficiency,
            close_location=close_location,
            opening_vwap=self._current_vwap,
            closes_above_vwap=sum(
                item == RetestSide.LONG.value for item in self._opening_vwap_sides
            ),
            closes_below_vwap=sum(
                item == RetestSide.SHORT.value for item in self._opening_vwap_sides
            ),
            overnight_high=self._overnight_high,
            overnight_low=self._overnight_low,
            prior_rth_high=self._prior_rth_high,
            prior_rth_low=self._prior_rth_low,
            prior_rth_close=self._prior_rth_close,
        )

    def _freeze_classification(self, timestamp_utc: str) -> RetestClassification:
        if self._classification is not None:
            return self._classification
        features = self._build_features(timestamp_utc)
        classification = classify_opening_auction_retest(features, self.config)
        self._current_features = features
        self._classification = classification
        self._session_snapshots.append(RetestSessionSnapshot(features, classification))
        self._entry_state = (
            RetestState.WAIT_FIRST_RETEST
            if classification.regime != RetestRegime.NO_TRADE
            else RetestState.DONE
        )
        self._event(
            timestamp_utc,
            "classified",
            metadata={
                "reason": classification.reason,
                "reference_side": classification.reference_side,
                "reference_type": classification.reference_type,
                "reference_price": classification.reference_price,
            },
        )
        return classification

    def _round_down(self, price: float) -> float:
        tick = self.config.tick_size
        return round(math.floor((price + 1e-12) / tick) * tick, 10)

    def _round_up(self, price: float) -> float:
        tick = self.config.tick_size
        return round(math.ceil((price - 1e-12) / tick) * tick, 10)

    def _cancel(self, bar: MarketBar, reason: str, **metadata: Any) -> None:
        self._entry_state = RetestState.DONE
        self._event(
            bar.timestamp_utc,
            "entry_cancelled",
            metadata={"reason": reason, **metadata},
        )

    def _reject(
        self, bar: MarketBar, reason: str, *, metadata: dict[str, Any]
    ) -> StrategyResult:
        classification = self._classification
        self._entry_attempted = True
        self._entry_state = RetestState.DONE
        self._event(
            bar.timestamp_utc,
            "entry_rejected",
            metadata={"reason": reason, **metadata},
        )
        return StrategyResult(
            signal=SignalDecision.rejected(
                timestamp_utc=bar.timestamp_utc,
                symbol=bar.symbol,
                side=(classification.side.value if classification else None),
                reason=reason,
                metadata=metadata,
            )
        )

    def _accepted_order(
        self,
        bar: MarketBar,
        *,
        stop_price: float,
        target_price: float,
        risk_points: float,
    ) -> StrategyResult:
        classification = self._classification
        features = self._current_features
        assert classification is not None and features is not None
        assert classification.reference_price is not None and features.dtr20 is not None
        reason = (
            ACCEPTED_BREAK_REASON
            if classification.regime == RetestRegime.ACCEPTED_BREAK
            else REJECTED_BREAK_REASON
        )
        intent_side = "buy" if classification.side == RetestSide.LONG else "sell"
        reward_points = abs(target_price - bar.close)
        metadata = {
            "regime": classification.regime.value,
            "classification_reason": classification.reason,
            "reference_side": classification.reference_side,
            "reference_type": classification.reference_type,
            "reference_price": classification.reference_price,
            "signal_price": bar.close,
            "stop_price": stop_price,
            "target_price": target_price,
            "decision_risk_points": risk_points,
            "decision_risk_dtr": risk_points / features.dtr20,
            "decision_reward_points": reward_points,
            "decision_reward_r": reward_points / risk_points,
            "dtr20": features.dtr20,
            "confirmation_bars": self._confirmation_bars_seen,
        }
        self._entry_attempted = True
        self._entry_pending = True
        self._entry_pending_age = 0
        self._entry_state = RetestState.ENTRY_PENDING
        self._event(
            bar.timestamp_utc,
            "entry_confirmed",
            metadata={"reason": reason, **metadata},
        )
        return StrategyResult(
            signal=SignalDecision.accepted(
                timestamp_utc=bar.timestamp_utc,
                symbol=bar.symbol,
                side=classification.side.value,
                reason=reason,
                metadata=metadata,
            ),
            order_intents=(
                OrderIntent.market_entry(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side=intent_side,
                    quantity=self.config.contracts,
                    reason=reason,
                    metadata=metadata,
                ),
            ),
        )

    def _first_retest(self, bar: MarketBar) -> None:
        classification = self._classification
        features = self._current_features
        assert classification is not None and features is not None
        assert classification.reference_price is not None and features.dtr20 is not None
        reference = classification.reference_price
        dtr = features.dtr20
        tolerance = self.config.retest_tolerance_dtr * dtr
        hold_margin = self.config.retest_hold_margin_dtr * dtr
        if classification.side == RetestSide.LONG:
            contact = bar.low <= reference + tolerance
        else:
            contact = bar.high >= reference - tolerance
        if not contact:
            return

        bar_range = bar.high - bar.low
        close_location = (bar.close - bar.low) / bar_range if bar_range > 0 else None
        if classification.side == RetestSide.LONG:
            valid = bool(
                close_location is not None
                and bar.close >= reference + hold_margin
                and close_location >= self.config.retest_bar_close_location_min
            )
        else:
            valid = bool(
                close_location is not None
                and bar.close <= reference - hold_margin
                and close_location <= 1.0 - self.config.retest_bar_close_location_min
            )
        if not valid:
            self._cancel(
                bar,
                "first_retest_failed_hold",
                reference_price=reference,
                close_location=close_location,
            )
            return

        self._retest_high = bar.high
        self._retest_low = bar.low
        self._structural_extreme = (
            bar.low if classification.side == RetestSide.LONG else bar.high
        )
        self._confirmation_bars_seen = 0
        self._entry_state = RetestState.ARMED
        self._event(
            bar.timestamp_utc,
            "first_retest_armed",
            metadata={
                "reference_price": reference,
                "retest_high": bar.high,
                "retest_low": bar.low,
                "close_location": close_location,
            },
        )

    def _confirmation(self, bar: MarketBar) -> Optional[StrategyResult]:
        classification = self._classification
        features = self._current_features
        assert classification is not None and features is not None
        assert classification.reference_price is not None and features.dtr20 is not None
        assert self._retest_high is not None and self._retest_low is not None
        assert self._structural_extreme is not None
        reference = classification.reference_price
        dtr = features.dtr20
        self._confirmation_bars_seen += 1
        margin = self.config.confirmation_reference_margin_dtr * dtr
        invalidation = self.config.invalidation_margin_dtr * dtr

        if classification.side == RetestSide.LONG:
            self._structural_extreme = min(self._structural_extreme, bar.low)
            if bar.close < reference - invalidation:
                self._cancel(bar, "armed_reference_invalidated", reference_price=reference)
                return None
            confirmed = bool(
                bar.close >= self._retest_high + self.config.tick_size
                and bar.close >= reference + margin
                and self._current_vwap is not None
                and bar.close > self._current_vwap
            )
        else:
            self._structural_extreme = max(self._structural_extreme, bar.high)
            if bar.close > reference + invalidation:
                self._cancel(bar, "armed_reference_invalidated", reference_price=reference)
                return None
            confirmed = bool(
                bar.close <= self._retest_low - self.config.tick_size
                and bar.close <= reference - margin
                and self._current_vwap is not None
                and bar.close < self._current_vwap
            )

        if not confirmed:
            if self._confirmation_bars_seen >= self.config.confirmation_max_bars:
                self._cancel(
                    bar,
                    "confirmation_expired",
                    confirmation_bars=self._confirmation_bars_seen,
                )
            return None

        if classification.side == RetestSide.LONG:
            stop = self._round_down(
                self._structural_extreme - self.config.stop_buffer_dtr * dtr
            )
            risk = bar.close - stop
            target = self._round_down(bar.close + self.config.reward_r * risk)
        else:
            stop = self._round_up(
                self._structural_extreme + self.config.stop_buffer_dtr * dtr
            )
            risk = stop - bar.close
            target = self._round_up(bar.close - self.config.reward_r * risk)
        risk_dtr = risk / dtr if dtr > 0 else 0.0
        if not self.config.min_risk_dtr <= risk_dtr <= self.config.max_risk_dtr:
            return self._reject(
                bar,
                "retest_risk_geometry",
                metadata={"risk_points": risk, "risk_dtr": risk_dtr},
            )
        return self._accepted_order(
            bar,
            stop_price=stop,
            target_price=target,
            risk_points=risk,
        )

    def _hard_exit(self, bar: MarketBar, minute: int) -> tuple[ExitDecision, ...]:
        signal_minute = self.config.hard_exit_fill_minutes_et - 1
        if (
            self._position_side is not None
            and not self._exit_pending
            and signal_minute <= minute < 18 * 60
        ):
            self._exit_pending = True
            branch = self._position_branch or "opening_auction_retest"
            self._event(bar.timestamp_utc, "time_exit_signalled")
            return (
                ExitDecision(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    reason=f"{branch}_time_exit",
                ),
            )
        return ()

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        session = classify_timestamp(bar.timestamp_utc)
        session_iso = session.session_date.isoformat()
        if session_iso != self._session_date:
            self._start_session(session.session_date)
        self._last_session_timestamp = bar.timestamp_utc

        if self._entry_pending:
            self._entry_pending_age += 1
            if self._entry_pending_age > 2:
                self._entry_pending = False
                self._entry_pending_age = 0
                self._entry_state = RetestState.DONE
                self._event(bar.timestamp_utc, "entry_pending_expired")

        minute = session.minutes_from_midnight_et
        exits = self._hard_exit(bar, minute)
        if not session.is_rth:
            if not self._rth_started:
                self._overnight_high = (
                    bar.high
                    if self._overnight_high is None
                    else max(self._overnight_high, bar.high)
                )
                self._overnight_low = (
                    bar.low
                    if self._overnight_low is None
                    else min(self._overnight_low, bar.low)
                )
                offset = minute - 18 * 60 if minute >= 18 * 60 else 6 * 60 + minute
                if not self._overnight_offsets or offset > self._overnight_offsets[-1]:
                    self._overnight_offsets.append(offset)
            return StrategyResult(exits=exits)

        self._rth_started = True
        self._rth_high = bar.high if self._rth_high is None else max(self._rth_high, bar.high)
        self._rth_low = bar.low if self._rth_low is None else min(self._rth_low, bar.low)
        self._rth_close = bar.close
        self._update_vwap(bar)
        if (
            self.config.observation_start_minutes_et
            <= minute
            < self.config.observation_end_minutes_et
        ):
            self._record_opening_bar(bar, minute)

        if (
            self._classification is None
            and minute == self.config.observation_end_minutes_et - 1
        ):
            self._freeze_classification(bar.timestamp_utc)
        elif self._classification is None and minute >= self.config.observation_end_minutes_et:
            self._freeze_classification(bar.timestamp_utc)

        result = StrategyResult(exits=exits)
        available = bool(
            self._classification is not None
            and self._classification.regime != RetestRegime.NO_TRADE
            and self._position_side is None
            and not self._entry_pending
            and not self._entry_attempted
        )
        if (
            available
            and self.config.observation_end_minutes_et
            <= minute
            < self.config.entry_end_minutes_et
        ):
            order: Optional[StrategyResult] = None
            if self._entry_state == RetestState.WAIT_FIRST_RETEST:
                if minute >= self.config.entry_end_minutes_et - 1:
                    self._cancel(bar, "first_retest_window_expired")
                else:
                    self._first_retest(bar)
            elif self._entry_state == RetestState.ARMED:
                order = self._confirmation(bar)
            if order is not None:
                result = StrategyResult(
                    signal=order.signal,
                    order_intents=order.order_intents,
                    exits=exits,
                )
        elif available and minute >= self.config.entry_end_minutes_et:
            if self._entry_state in (
                RetestState.WAIT_FIRST_RETEST,
                RetestState.ARMED,
            ):
                self._cancel(bar, "entry_window_expired")
        return result
