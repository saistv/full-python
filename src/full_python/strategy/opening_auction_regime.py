"""Opening Auction Regime v1.

A causal, one-trade-per-session auction state machine. The first completed
15 RTH minutes are classified once as initiative acceptance, failed auction,
or no-trade. Initiative entries require a later pullback and re-acceleration;
failed auctions may enter at the next bar open. The simulator remains the sole
authority for fills, frozen brackets, costs, and exchange-calendar flattening.
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
from full_python.strategy.opening_auction_regime_config import (
    OpeningAuctionRegimeConfig,
)


CONTINUATION_REASON = "opening_auction_continuation"
FAILED_AUCTION_REASON = "opening_auction_failed_auction"


class AuctionRegime(str, Enum):
    INITIATIVE = "initiative"
    FAILED_AUCTION = "failed_auction"
    NO_TRADE = "no_trade"


class AuctionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    NONE = "none"


@dataclass(frozen=True)
class OpeningAuctionFeatures:
    session_date: str
    classification_timestamp_utc: str
    complete_observation: bool
    roll_transition: bool
    complete_overnight: bool
    overnight_bar_count: int
    overnight_max_gap_minutes: Optional[int]
    opening_minutes: tuple[int, ...]
    dtr20: Optional[float]
    opening_volume_ratio: Optional[float]
    rth_open: Optional[float]
    opening_high: Optional[float]
    opening_low: Optional[float]
    opening_close: Optional[float]
    opening_width: Optional[float]
    opening_midpoint: Optional[float]
    efficiency_ratio: Optional[float]
    close_location: Optional[float]
    opening_vwap: Optional[float]
    closes_above_vwap: int
    closes_below_vwap: int
    last_vwap_sides: tuple[str, ...]
    overnight_high: Optional[float]
    overnight_low: Optional[float]
    prior_rth_high: Optional[float]
    prior_rth_low: Optional[float]
    prior_rth_close: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuctionClassification:
    regime: AuctionRegime
    side: AuctionSide
    reason: str
    reference_type: Optional[str] = None
    reference_price: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "side": self.side.value,
            "reason": self.reason,
            "reference_type": self.reference_type,
            "reference_price": self.reference_price,
        }


@dataclass(frozen=True)
class AuctionSessionSnapshot:
    features: OpeningAuctionFeatures
    classification: AuctionClassification

    def to_dict(self) -> dict[str, Any]:
        return {**self.features.to_dict(), **self.classification.to_dict()}


@dataclass(frozen=True)
class AuctionDiagnosticEvent:
    session_date: str
    timestamp_utc: str
    event: str
    regime: str
    side: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _external_reference(
    overnight: float, prior_rth: float, *, side: str
) -> tuple[str, float]:
    """Return the outermost comparable external level and an audit label."""
    if overnight == prior_rth:
        return (f"overnight_and_prior_{side}", overnight)
    if side == "low":
        return (
            ("overnight_low", overnight)
            if overnight < prior_rth
            else ("prior_rth_low", prior_rth)
        )
    return (
        ("overnight_high", overnight)
        if overnight > prior_rth
        else ("prior_rth_high", prior_rth)
    )


def classify_opening_auction(
    features: OpeningAuctionFeatures,
    config: OpeningAuctionRegimeConfig,
) -> AuctionClassification:
    """Pure classifier for the frozen 09:30-09:44 opening observation."""
    if not features.complete_observation:
        return AuctionClassification(
            AuctionRegime.NO_TRADE, AuctionSide.NONE, "incomplete_opening_observation"
        )
    if features.roll_transition:
        return AuctionClassification(
            AuctionRegime.NO_TRADE, AuctionSide.NONE, "continuous_contract_roll"
        )
    if not features.complete_overnight:
        return AuctionClassification(
            AuctionRegime.NO_TRADE, AuctionSide.NONE, "incomplete_overnight_coverage"
        )

    required = (
        features.dtr20,
        features.opening_volume_ratio,
        features.rth_open,
        features.opening_high,
        features.opening_low,
        features.opening_close,
        features.opening_width,
        features.opening_midpoint,
        features.efficiency_ratio,
        features.close_location,
        features.opening_vwap,
        features.overnight_high,
        features.overnight_low,
        features.prior_rth_high,
        features.prior_rth_low,
        features.prior_rth_close,
    )
    if any(value is None or not math.isfinite(value) for value in required):
        return AuctionClassification(
            AuctionRegime.NO_TRADE, AuctionSide.NONE, "missing_reference_history"
        )

    dtr = float(features.dtr20)
    width = float(features.opening_width)
    if dtr <= 0 or width <= 0:
        return AuctionClassification(
            AuctionRegime.NO_TRADE, AuctionSide.NONE, "invalid_opening_scale"
        )
    if float(features.opening_volume_ratio) < config.opening_volume_ratio_min:
        return AuctionClassification(
            AuctionRegime.NO_TRADE, AuctionSide.NONE, "opening_participation_below_threshold"
        )

    open_price = float(features.rth_open)
    high = float(features.opening_high)
    low = float(features.opening_low)
    close = float(features.opening_close)
    efficiency = float(features.efficiency_ratio)
    close_location = float(features.close_location)
    required_acceptance = math.ceil(
        config.initiative_vwap_acceptance_fraction * config.observation_minutes
    )

    candidates: list[AuctionClassification] = []

    initiative_long = (
        close - open_price >= config.initiative_displacement_dtr * dtr
        and efficiency >= config.initiative_efficiency_min
        and close_location >= config.initiative_close_location_min
        and features.closes_above_vwap >= required_acceptance
        and close
        >= float(features.overnight_high) + config.initiative_external_break_dtr * dtr
    )
    if initiative_long:
        candidates.append(
            AuctionClassification(
                AuctionRegime.INITIATIVE,
                AuctionSide.LONG,
                "initiative_acceptance",
                "overnight_high",
                float(features.overnight_high),
            )
        )

    initiative_short = (
        open_price - close >= config.initiative_displacement_dtr * dtr
        and efficiency >= config.initiative_efficiency_min
        and close_location <= 1.0 - config.initiative_close_location_min
        and features.closes_below_vwap >= required_acceptance
        and close
        <= float(features.overnight_low) - config.initiative_external_break_dtr * dtr
    )
    if initiative_short:
        candidates.append(
            AuctionClassification(
                AuctionRegime.INITIATIVE,
                AuctionSide.SHORT,
                "initiative_acceptance",
                "overnight_low",
                float(features.overnight_low),
            )
        )

    low_label, low_reference = _external_reference(
        float(features.overnight_low), float(features.prior_rth_low), side="low"
    )
    last_n = features.last_vwap_sides[-config.failure_vwap_reclaim_bars :]
    failed_low = (
        len(last_n) == config.failure_vwap_reclaim_bars
        and all(item == AuctionSide.LONG.value for item in last_n)
        and low <= low_reference - config.failure_breach_dtr * dtr
        and close >= low_reference + config.failure_reclaim_dtr * dtr
        and close_location >= config.failure_close_location_min
    )
    if failed_low:
        candidates.append(
            AuctionClassification(
                AuctionRegime.FAILED_AUCTION,
                AuctionSide.LONG,
                "failed_low_reclaimed",
                low_label,
                low_reference,
            )
        )

    high_label, high_reference = _external_reference(
        float(features.overnight_high), float(features.prior_rth_high), side="high"
    )
    failed_high = (
        len(last_n) == config.failure_vwap_reclaim_bars
        and all(item == AuctionSide.SHORT.value for item in last_n)
        and high >= high_reference + config.failure_breach_dtr * dtr
        and close <= high_reference - config.failure_reclaim_dtr * dtr
        and close_location <= 1.0 - config.failure_close_location_min
    )
    if failed_high:
        candidates.append(
            AuctionClassification(
                AuctionRegime.FAILED_AUCTION,
                AuctionSide.SHORT,
                "failed_high_reclaimed",
                high_label,
                high_reference,
            )
        )

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return AuctionClassification(
            AuctionRegime.NO_TRADE, AuctionSide.NONE, "conflicting_auction_evidence"
        )
    return AuctionClassification(
        AuctionRegime.NO_TRADE, AuctionSide.NONE, "weak_opening_evidence"
    )


class OpeningAuctionRegimeStrategy:
    def __init__(self, config: OpeningAuctionRegimeConfig) -> None:
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

        self._session_snapshots: list[AuctionSessionSnapshot] = []
        self._diagnostic_events: list[AuctionDiagnosticEvent] = []
        self._bar_index = -1
        self._previous_bar: Optional[MarketBar] = None

        self._position_side: Optional[str] = None
        self._position_branch: Optional[str] = None
        self._entry_pending = False
        self._entry_pending_age = 0
        self._exit_pending = False

        self._reset_session_state()

    @property
    def session_diagnostics(self) -> tuple[AuctionSessionSnapshot, ...]:
        return tuple(self._session_snapshots)

    @property
    def diagnostic_events(self) -> tuple[AuctionDiagnosticEvent, ...]:
        return tuple(self._diagnostic_events)

    def on_fill(self, fill: Fill) -> None:
        if fill.reason not in (CONTINUATION_REASON, FAILED_AUCTION_REASON):
            return
        self._position_side = AuctionSide.LONG.value if fill.side == "buy" else AuctionSide.SHORT.value
        self._position_branch = (
            AuctionRegime.INITIATIVE.value
            if fill.reason == CONTINUATION_REASON
            else AuctionRegime.FAILED_AUCTION.value
        )
        self._entry_pending = False
        self._entry_pending_age = 0
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

    def _event(
        self, timestamp_utc: str, event: str, *, metadata: Optional[dict[str, Any]] = None
    ) -> None:
        classification = self._classification
        self._diagnostic_events.append(
            AuctionDiagnosticEvent(
                session_date=self._session_date or "unknown",
                timestamp_utc=timestamp_utc,
                event=event,
                regime=(classification.regime.value if classification else AuctionRegime.NO_TRADE.value),
                side=(classification.side.value if classification else AuctionSide.NONE.value),
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

        self._classification: Optional[AuctionClassification] = None
        self._current_features: Optional[OpeningAuctionFeatures] = None
        self._entry_attempted = False
        self._continuation_armed = False
        self._continuation_cancelled = False
        self._armed_bar_index: Optional[int] = None
        self._pullback_extreme: Optional[float] = None

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
            # A true range is causal only when the preceding RTH close exists.
            # Also do not mix the old contract's close with the new contract's
            # RTH range on a continuous-contract roll transition.
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

    def _update_vwap(self, bar: MarketBar) -> Optional[float]:
        volume = max(0.0, bar.volume)
        typical = (bar.high + bar.low + bar.close) / 3.0
        self._vwap_pv += typical * volume
        self._vwap_volume += volume
        if self._vwap_volume > 0:
            self._current_vwap = self._vwap_pv / self._vwap_volume
        return self._current_vwap

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
            side = AuctionSide.LONG.value
        else:
            side = AuctionSide.SHORT.value
        self._opening_vwap_sides.append(side)

    def _build_features(self, timestamp_utc: str) -> OpeningAuctionFeatures:
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
        if self._opening_high is not None and self._opening_low is not None:
            width = self._opening_high - self._opening_low
            midpoint = (self._opening_high + self._opening_low) / 2.0
        if width is not None and width > 0 and self._opening_close is not None:
            close_location = (self._opening_close - float(self._opening_low)) / width
        if self._rth_open is not None and self._opening_close is not None:
            prior = self._rth_open
            path = 0.0
            for close in self._opening_closes:
                path += abs(close - prior)
                prior = close
            if path > 0:
                efficiency = abs(self._opening_close - self._rth_open) / path

        dtr20 = (
            statistics.median(self._dtr_history)
            if len(self._dtr_history) == self.config.daily_range_lookback_sessions
            else None
        )
        volume_ratio: Optional[float] = None
        if (
            len(self._opening_volume_history)
            == self.config.opening_volume_lookback_sessions
        ):
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

        return OpeningAuctionFeatures(
            session_date=self._session_date or "unknown",
            classification_timestamp_utc=timestamp_utc,
            complete_observation=complete,
            roll_transition=self._roll_transition,
            complete_overnight=complete_overnight,
            overnight_bar_count=len(self._overnight_offsets),
            overnight_max_gap_minutes=overnight_max_gap,
            opening_minutes=tuple(self._opening_minutes),
            dtr20=dtr20,
            opening_volume_ratio=volume_ratio,
            rth_open=self._rth_open,
            opening_high=self._opening_high,
            opening_low=self._opening_low,
            opening_close=self._opening_close,
            opening_width=width,
            opening_midpoint=midpoint,
            efficiency_ratio=efficiency,
            close_location=close_location,
            opening_vwap=self._current_vwap,
            closes_above_vwap=sum(
                item == AuctionSide.LONG.value for item in self._opening_vwap_sides
            ),
            closes_below_vwap=sum(
                item == AuctionSide.SHORT.value for item in self._opening_vwap_sides
            ),
            last_vwap_sides=tuple(self._opening_vwap_sides),
            overnight_high=self._overnight_high,
            overnight_low=self._overnight_low,
            prior_rth_high=self._prior_rth_high,
            prior_rth_low=self._prior_rth_low,
            prior_rth_close=self._prior_rth_close,
        )

    def _freeze_classification(self, timestamp_utc: str) -> AuctionClassification:
        if self._classification is not None:
            return self._classification
        features = self._build_features(timestamp_utc)
        classification = classify_opening_auction(features, self.config)
        self._current_features = features
        self._classification = classification
        self._session_snapshots.append(AuctionSessionSnapshot(features, classification))
        self._event(
            timestamp_utc,
            "classified",
            metadata={
                "reason": classification.reason,
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

    def _rejected(
        self,
        bar: MarketBar,
        reason: str,
        *,
        metadata: dict[str, Any],
    ) -> StrategyResult:
        side = self._classification.side.value if self._classification else None
        self._entry_attempted = True
        self._event(bar.timestamp_utc, "entry_rejected", metadata={"reason": reason, **metadata})
        return StrategyResult(
            signal=SignalDecision.rejected(
                timestamp_utc=bar.timestamp_utc,
                symbol=bar.symbol,
                side=side,
                reason=reason,
                metadata=metadata,
            )
        )

    def _accepted_order(
        self,
        bar: MarketBar,
        *,
        reason: str,
        stop_price: float,
        target_price: float,
        metadata: dict[str, Any],
    ) -> StrategyResult:
        classification = self._classification
        assert classification is not None
        intent_side = "buy" if classification.side == AuctionSide.LONG else "sell"
        combined = {
            **metadata,
            "regime": classification.regime.value,
            "classification_reason": classification.reason,
            "reference_type": classification.reference_type,
            "reference_price": classification.reference_price,
            "stop_price": stop_price,
            "target_price": target_price,
        }
        self._entry_attempted = True
        self._entry_pending = True
        self._entry_pending_age = 0
        self._event(
            bar.timestamp_utc,
            "entry_confirmed",
            metadata={"reason": reason, **combined},
        )
        return StrategyResult(
            signal=SignalDecision.accepted(
                timestamp_utc=bar.timestamp_utc,
                symbol=bar.symbol,
                side=classification.side.value,
                reason=reason,
                metadata=combined,
            ),
            order_intents=(
                OrderIntent.market_entry(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side=intent_side,
                    quantity=self.config.contracts,
                    reason=reason,
                    metadata={
                        "signal_price": bar.close,
                        "stop_price": stop_price,
                        "target_price": target_price,
                        "regime": classification.regime.value,
                        "classification_reason": classification.reason,
                        "reference_type": classification.reference_type,
                        "reference_price": classification.reference_price,
                    },
                ),
            ),
        )

    def _failed_auction_order(self, bar: MarketBar) -> StrategyResult:
        features = self._current_features
        classification = self._classification
        assert features is not None and classification is not None
        assert features.dtr20 is not None
        assert features.prior_rth_close is not None
        assert features.opening_low is not None and features.opening_high is not None
        dtr = features.dtr20
        if classification.side == AuctionSide.LONG:
            stop = self._round_down(
                features.opening_low - self.config.failure_stop_buffer_dtr * dtr
            )
            risk = bar.close - stop
        else:
            stop = self._round_up(
                features.opening_high + self.config.failure_stop_buffer_dtr * dtr
            )
            risk = stop - bar.close
        risk_dtr = risk / dtr if dtr > 0 else 0.0
        if not self.config.failure_min_risk_dtr <= risk_dtr <= self.config.failure_max_risk_dtr:
            return self._rejected(
                bar,
                "failed_auction_risk_geometry",
                metadata={"risk_points": risk, "risk_dtr": risk_dtr},
            )

        prior_close = features.prior_rth_close
        if classification.side == AuctionSide.LONG:
            if prior_close <= bar.close:
                return self._rejected(
                    bar,
                    "failed_auction_target_wrong_side",
                    metadata={"prior_rth_close": prior_close, "signal_price": bar.close},
                )
            target = self._round_down(
                min(prior_close, bar.close + self.config.failure_max_reward_r * risk)
            )
            reward = target - bar.close
        else:
            if prior_close >= bar.close:
                return self._rejected(
                    bar,
                    "failed_auction_target_wrong_side",
                    metadata={"prior_rth_close": prior_close, "signal_price": bar.close},
                )
            target = self._round_up(
                max(prior_close, bar.close - self.config.failure_max_reward_r * risk)
            )
            reward = bar.close - target
        reward_r = reward / risk if risk > 0 else 0.0
        if reward_r < self.config.failure_min_reward_r:
            return self._rejected(
                bar,
                "failed_auction_target_too_close",
                metadata={
                    "reward_points": reward,
                    "reward_r": reward_r,
                    "prior_rth_close": prior_close,
                },
            )
        return self._accepted_order(
            bar,
            reason=FAILED_AUCTION_REASON,
            stop_price=stop,
            target_price=target,
            metadata={
                "decision_risk_points": risk,
                "decision_reward_points": reward,
                "decision_reward_r": reward_r,
                "dtr20": dtr,
                "opening_volume_ratio": features.opening_volume_ratio,
            },
        )

    def _continuation_order(self, bar: MarketBar) -> Optional[StrategyResult]:
        features = self._current_features
        classification = self._classification
        assert features is not None and classification is not None
        assert features.dtr20 is not None
        assert features.opening_width is not None
        assert features.opening_midpoint is not None
        assert features.opening_high is not None and features.opening_low is not None
        if self._current_vwap is None:
            return None

        midpoint = features.opening_midpoint
        if classification.side == AuctionSide.LONG:
            if bar.close <= midpoint:
                self._continuation_cancelled = True
                self._event(bar.timestamp_utc, "continuation_cancelled", metadata={"reason": "midpoint_lost"})
                return None
            pullback = features.opening_high - bar.low
            close_holds = bar.close > midpoint and bar.close > self._current_vwap
        else:
            if bar.close >= midpoint:
                self._continuation_cancelled = True
                self._event(bar.timestamp_utc, "continuation_cancelled", metadata={"reason": "midpoint_lost"})
                return None
            pullback = bar.high - features.opening_low
            close_holds = bar.close < midpoint and bar.close < self._current_vwap

        required_pullback = self.config.continuation_pullback_fraction * features.opening_width
        if not self._continuation_armed:
            if pullback >= required_pullback and close_holds:
                self._continuation_armed = True
                self._armed_bar_index = self._bar_index
                self._pullback_extreme = (
                    bar.low if classification.side == AuctionSide.LONG else bar.high
                )
                self._event(
                    bar.timestamp_utc,
                    "continuation_armed",
                    metadata={
                        "pullback_points": pullback,
                        "required_pullback_points": required_pullback,
                    },
                )
            return None

        if classification.side == AuctionSide.LONG:
            self._pullback_extreme = min(float(self._pullback_extreme), bar.low)
            confirmed = (
                self._bar_index > int(self._armed_bar_index)
                and self._previous_bar is not None
                and bar.close > self._previous_bar.high
                and bar.close > self._current_vwap
            )
        else:
            self._pullback_extreme = max(float(self._pullback_extreme), bar.high)
            confirmed = (
                self._bar_index > int(self._armed_bar_index)
                and self._previous_bar is not None
                and bar.close < self._previous_bar.low
                and bar.close < self._current_vwap
            )
        if not confirmed:
            return None

        dtr = features.dtr20
        if classification.side == AuctionSide.LONG:
            stop = self._round_down(
                float(self._pullback_extreme)
                - self.config.continuation_stop_buffer_dtr * dtr
            )
            risk = bar.close - stop
            target = self._round_down(bar.close + self.config.continuation_reward_r * risk)
            reward = target - bar.close
        else:
            stop = self._round_up(
                float(self._pullback_extreme)
                + self.config.continuation_stop_buffer_dtr * dtr
            )
            risk = stop - bar.close
            target = self._round_up(bar.close - self.config.continuation_reward_r * risk)
            reward = bar.close - target
        risk_dtr = risk / dtr if dtr > 0 else 0.0
        if not (
            self.config.continuation_min_risk_dtr
            <= risk_dtr
            <= self.config.continuation_max_risk_dtr
        ):
            return self._rejected(
                bar,
                "continuation_risk_geometry",
                metadata={"risk_points": risk, "risk_dtr": risk_dtr},
            )
        return self._accepted_order(
            bar,
            reason=CONTINUATION_REASON,
            stop_price=stop,
            target_price=target,
            metadata={
                "decision_risk_points": risk,
                "decision_reward_points": reward,
                "decision_reward_r": reward / risk if risk > 0 else None,
                "dtr20": dtr,
                "pullback_extreme": self._pullback_extreme,
                "opening_volume_ratio": features.opening_volume_ratio,
            },
        )

    def _hard_exit(self, bar: MarketBar, minute: int) -> tuple[ExitDecision, ...]:
        signal_minute = self.config.hard_exit_fill_minutes_et - 1
        if (
            self._position_side is not None
            and not self._exit_pending
            and signal_minute <= minute < 18 * 60
        ):
            self._exit_pending = True
            branch = self._position_branch or "opening_auction"
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
        self._bar_index += 1
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
                offset = (
                    minute - 18 * 60
                    if minute >= 18 * 60
                    else 6 * 60 + minute
                )
                if not self._overnight_offsets or offset > self._overnight_offsets[-1]:
                    self._overnight_offsets.append(offset)
            self._previous_bar = bar
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

        just_classified = False
        if (
            self._classification is None
            and minute == self.config.observation_end_minutes_et - 1
        ):
            self._freeze_classification(bar.timestamp_utc)
            just_classified = True
        elif self._classification is None and minute >= self.config.observation_end_minutes_et:
            self._freeze_classification(bar.timestamp_utc)
            just_classified = True

        result = StrategyResult(exits=exits)
        if (
            just_classified
            and self._classification is not None
            and self._classification.regime == AuctionRegime.FAILED_AUCTION
            and self._position_side is None
            and not self._entry_pending
            and not self._entry_attempted
        ):
            order = self._failed_auction_order(bar)
            result = StrategyResult(
                signal=order.signal,
                order_intents=order.order_intents,
                exits=exits,
            )
        elif (
            self._classification is not None
            and self._classification.regime == AuctionRegime.INITIATIVE
            and self._position_side is None
            and not self._entry_attempted
            and not self._entry_pending
            and not self._continuation_cancelled
            and self.config.observation_end_minutes_et
            <= minute
            < self.config.continuation_entry_end_minutes_et
        ):
            order = self._continuation_order(bar)
            if order is not None:
                result = StrategyResult(
                    signal=order.signal,
                    order_intents=order.order_intents,
                    exits=exits,
                )
        elif (
            self._classification is not None
            and self._classification.regime == AuctionRegime.INITIATIVE
            and not self._entry_attempted
            and not self._continuation_cancelled
            and minute >= self.config.continuation_entry_end_minutes_et
        ):
            self._continuation_cancelled = True
            self._event(bar.timestamp_utc, "continuation_cancelled", metadata={"reason": "entry_window_expired"})

        self._previous_bar = bar
        return result
