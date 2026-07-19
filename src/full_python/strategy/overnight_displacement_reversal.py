"""Overnight Displacement Reversal v3.

The strategy is a causal, one-attempt state machine.  A materially displaced
RTH open must first extend away from the prior complete RTH close and then
produce the first decisive close back through the 09:30 open.  The simulator
owns next-open fills and the frozen bracket.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from enum import Enum
import math
import statistics
from typing import Any, Deque, Optional

from full_python.data.databento import front_contract_for_session
from full_python.data.exchange_calendar import rth_close_minutes_et
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
from full_python.strategy.overnight_displacement_reversal_config import (
    OvernightDisplacementReversalConfig,
)


OVERNIGHT_DISPLACEMENT_REVERSAL_REASON = "overnight_displacement_reversal"
ODR_BRANCH = OVERNIGHT_DISPLACEMENT_REVERSAL_REASON


class DisplacementRegime(str, Enum):
    ELIGIBLE_GAP = "eligible_gap"
    NO_TRADE = "no_trade"


class DisplacementSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    NONE = "none"


class DisplacementState(str, Enum):
    OBSERVE_OVERNIGHT = "observe_overnight"
    WAIT_EXTENSION = "wait_extension"
    WAIT_REJECTION = "wait_rejection"
    ENTRY_PENDING = "entry_pending"
    POSITION = "position"
    DONE = "done"


@dataclass(frozen=True)
class OvernightDisplacementFeatures:
    session_date: str
    classification_timestamp_utc: str
    setup_id: str
    current_rth_session: bool
    prior_rth_session_date: Optional[str]
    prior_rth_contract: Optional[str]
    current_contract: Optional[str]
    prior_rth_complete: bool
    prior_rth_all_finite: bool
    prior_rth_expected_minutes: int
    prior_rth_observed_minutes: int
    roll_transition: bool
    complete_overnight: bool
    overnight_bar_count: int
    overnight_first_offset_minutes: Optional[int]
    overnight_last_offset_minutes: Optional[int]
    overnight_max_gap_minutes: Optional[int]
    overnight_all_finite: bool
    overnight_total_volume: float
    overnight_high: Optional[float]
    overnight_low: Optional[float]
    overnight_close: Optional[float]
    overnight_range: Optional[float]
    overnight_vwap: Optional[float]
    prior_rth_close: Optional[float]
    dtr20: Optional[float]
    dtr_session_dates: tuple[str, ...]
    dtr_values: tuple[float, ...]
    rth_open: Optional[float]
    gap_signed_points: Optional[float]
    gap_dtr: Optional[float]
    gap_direction: Optional[str]
    breadth_above_prior_close: Optional[float]
    breadth_below_prior_close: Optional[float]
    displacement_breadth: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OvernightDisplacementClassification:
    regime: DisplacementRegime
    side: DisplacementSide
    reason: str
    setup_id: str
    gap_direction: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "side": self.side.value,
            "reason": self.reason,
            "setup_id": self.setup_id,
            "gap_direction": self.gap_direction,
        }


@dataclass(frozen=True)
class OvernightDisplacementSessionSnapshot:
    features: OvernightDisplacementFeatures
    classification: OvernightDisplacementClassification

    def to_dict(self) -> dict[str, Any]:
        return {**self.features.to_dict(), **self.classification.to_dict()}


@dataclass(frozen=True)
class OvernightDisplacementDiagnosticEvent:
    session_date: str
    timestamp_utc: str
    event: str
    setup_id: str
    branch: str
    gap_direction: Optional[str]
    side: str
    state: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _correction_side(
    rth_open: Optional[float], prior_rth_close: Optional[float]
) -> DisplacementSide:
    if rth_open is None or prior_rth_close is None:
        return DisplacementSide.NONE
    if not math.isfinite(float(rth_open)) or not math.isfinite(float(prior_rth_close)):
        return DisplacementSide.NONE
    if rth_open > prior_rth_close:
        return DisplacementSide.SHORT
    if rth_open < prior_rth_close:
        return DisplacementSide.LONG
    return DisplacementSide.NONE


def _setup_id(session_date: str, side: DisplacementSide) -> str:
    return f"odr-v3:{session_date}:{side.value}"


def _previous_expected_rth(day: date) -> date:
    cursor = day - timedelta(days=1)
    while rth_close_minutes_et(cursor) is None:
        cursor -= timedelta(days=1)
    return cursor


def classify_overnight_displacement_reversal(
    features: OvernightDisplacementFeatures,
    config: OvernightDisplacementReversalConfig,
) -> OvernightDisplacementClassification:
    """Pure mirrored eligibility classifier frozen at the 09:30 bar."""
    side = _correction_side(features.rth_open, features.prior_rth_close)
    setup_id = _setup_id(features.session_date, side)

    def no_trade(reason: str) -> OvernightDisplacementClassification:
        return OvernightDisplacementClassification(
            DisplacementRegime.NO_TRADE,
            side,
            reason,
            setup_id,
            features.gap_direction,
        )

    try:
        current_day = date.fromisoformat(features.session_date)
    except (TypeError, ValueError):
        return no_trade("invalid_current_session_date")
    expected_current_rth = rth_close_minutes_et(current_day) is not None
    if features.current_rth_session != expected_current_rth:
        return no_trade("inconsistent_current_rth_calendar")
    if not expected_current_rth:
        return no_trade("closed_current_rth_session")

    if features.prior_rth_session_date is None:
        return no_trade("incomplete_prior_rth_session")
    try:
        prior_day = date.fromisoformat(features.prior_rth_session_date)
    except (TypeError, ValueError):
        return no_trade("invalid_prior_rth_session_date")
    if prior_day != _previous_expected_rth(current_day):
        return no_trade("stale_prior_rth_session")
    prior_close_minute = rth_close_minutes_et(prior_day)
    expected_prior_minutes = (
        0
        if prior_close_minute is None
        else prior_close_minute - config.rth_open_minutes_et
    )
    computed_prior_complete = bool(
        expected_prior_minutes > 0
        and features.prior_rth_all_finite
        and features.prior_rth_expected_minutes == expected_prior_minutes
        and features.prior_rth_observed_minutes == expected_prior_minutes
    )
    if features.prior_rth_complete != computed_prior_complete:
        return no_trade("inconsistent_prior_rth_coverage")
    if not computed_prior_complete:
        return no_trade("incomplete_prior_rth_session")

    if features.prior_rth_contract is None or features.current_contract is None:
        return no_trade("missing_contract_identity")
    expected_prior_contract = front_contract_for_session(
        prior_day, root=config.contract_root
    )
    expected_current_contract = front_contract_for_session(
        current_day, root=config.contract_root
    )
    if (
        features.prior_rth_contract != expected_prior_contract
        or features.current_contract != expected_current_contract
    ):
        return no_trade("inconsistent_contract_identity")
    computed_roll = features.prior_rth_contract != features.current_contract
    if features.roll_transition != computed_roll:
        return no_trade("inconsistent_roll_geometry")
    if computed_roll:
        return no_trade("continuous_contract_roll")

    computed_overnight_complete = bool(
        features.overnight_bar_count >= 2
        and features.overnight_first_offset_minutes is not None
        and features.overnight_last_offset_minutes is not None
        and features.overnight_max_gap_minutes is not None
        and 0
        <= features.overnight_first_offset_minutes
        <= features.overnight_last_offset_minutes
        <= 929
        and features.overnight_max_gap_minutes >= 1
        and features.overnight_first_offset_minutes
        <= config.overnight_start_tolerance_minutes
        and features.overnight_last_offset_minutes
        >= 929 - config.overnight_preopen_tolerance_minutes
        and features.overnight_max_gap_minutes <= config.overnight_max_gap_minutes
        and features.overnight_all_finite
        and math.isfinite(features.overnight_total_volume)
        and features.overnight_total_volume > 0
    )
    if features.complete_overnight != computed_overnight_complete:
        return no_trade("inconsistent_overnight_coverage")
    if not computed_overnight_complete:
        return no_trade("incomplete_overnight_coverage")

    base_required = (
        features.prior_rth_close,
        features.dtr20,
        features.rth_open,
        features.overnight_high,
        features.overnight_low,
        features.overnight_close,
        features.overnight_range,
        features.overnight_vwap,
        features.overnight_total_volume,
        features.breadth_above_prior_close,
        features.breadth_below_prior_close,
    )
    if any(value is None or not math.isfinite(float(value)) for value in base_required):
        return no_trade("missing_or_nonfinite_reference_state")
    if float(features.dtr20) <= 0:
        return no_trade("invalid_daily_range_scale")
    if (
        len(features.dtr_values) != config.daily_range_lookback_sessions
        or len(features.dtr_session_dates) != config.daily_range_lookback_sessions
    ):
        return no_trade("incomplete_dtr_provenance")
    if any(not math.isfinite(value) or value <= 0 for value in features.dtr_values):
        return no_trade("invalid_dtr_provenance")
    try:
        dtr_days = tuple(date.fromisoformat(item) for item in features.dtr_session_dates)
    except (TypeError, ValueError):
        return no_trade("invalid_dtr_provenance")
    if (
        tuple(sorted(dtr_days)) != dtr_days
        or len(set(dtr_days)) != len(dtr_days)
        or any(day >= current_day for day in dtr_days)
        or any(rth_close_minutes_et(day) is None for day in dtr_days)
        or any(
            front_contract_for_session(day, root=config.contract_root)
            != front_contract_for_session(
                _previous_expected_rth(day), root=config.contract_root
            )
            for day in dtr_days
        )
        or not math.isclose(
            statistics.median(features.dtr_values),
            float(features.dtr20),
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
    ):
        return no_trade("inconsistent_dtr_provenance")
    if float(features.overnight_total_volume) <= 0:
        return no_trade("nonpositive_overnight_volume")
    if side == DisplacementSide.NONE:
        return no_trade("zero_gap_no_side")

    redundant_required = (
        features.gap_signed_points,
        features.gap_dtr,
        features.displacement_breadth,
    )
    if any(
        value is None or not math.isfinite(float(value))
        for value in redundant_required
    ):
        return no_trade("missing_or_nonfinite_gap_geometry")
    above = float(features.breadth_above_prior_close)
    below = float(features.breadth_below_prior_close)
    if not 0 <= above <= 1 or not 0 <= below <= 1 or above + below > 1 + 1e-12:
        return no_trade("invalid_breadth_geometry")
    signed_gap = float(features.rth_open) - float(features.prior_rth_close)
    expected_direction = "up" if signed_gap > 0 else "down"
    expected_gap_dtr = abs(signed_gap) / float(features.dtr20)
    expected_breadth = above if expected_direction == "up" else below
    if (
        features.setup_id != setup_id
        or features.gap_direction != expected_direction
        or not math.isclose(
            float(features.gap_signed_points), signed_gap, rel_tol=1e-12, abs_tol=1e-12
        )
        or not math.isclose(
            float(features.gap_dtr), expected_gap_dtr, rel_tol=1e-12, abs_tol=1e-12
        )
    ):
        return no_trade("inconsistent_gap_geometry")
    if not math.isclose(
        float(features.displacement_breadth),
        expected_breadth,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        return no_trade("inconsistent_displacement_breadth")

    gap_dtr = float(features.gap_dtr)
    if gap_dtr < config.min_gap_dtr:
        return no_trade("gap_below_minimum")
    if gap_dtr > config.max_gap_dtr:
        return no_trade("gap_above_maximum")
    if float(features.displacement_breadth) < config.min_displacement_breadth:
        return no_trade("displacement_breadth_below_minimum")
    return OvernightDisplacementClassification(
        DisplacementRegime.ELIGIBLE_GAP,
        side,
        "eligible_overnight_displacement",
        setup_id,
        features.gap_direction,
    )


class OvernightDisplacementReversalStrategy:
    def __init__(self, config: OvernightDisplacementReversalConfig) -> None:
        self.config = config
        self._dtr_history: Deque[float] = deque(
            maxlen=config.daily_range_lookback_sessions
        )
        self._dtr_session_dates: Deque[str] = deque(
            maxlen=config.daily_range_lookback_sessions
        )
        self._session_date: Optional[str] = None
        self._current_contract: Optional[str] = None
        self._previous_rth_contract: Optional[str] = None
        self._prior_rth_session_date: Optional[str] = None
        self._prior_rth_complete = False
        self._prior_rth_all_finite = False
        self._prior_rth_expected_minutes = 0
        self._prior_rth_observed_minutes = 0
        self._prior_rth_close: Optional[float] = None
        self._session_snapshots: list[OvernightDisplacementSessionSnapshot] = []
        self._diagnostic_events: list[OvernightDisplacementDiagnosticEvent] = []
        self._position_side: Optional[str] = None
        self._entry_pending = False
        self._entry_pending_age = 0
        self._exit_pending = False
        self._reset_session_state()

    @property
    def session_diagnostics(self) -> tuple[OvernightDisplacementSessionSnapshot, ...]:
        return tuple(self._session_snapshots)

    @property
    def diagnostic_events(self) -> tuple[OvernightDisplacementDiagnosticEvent, ...]:
        return tuple(self._diagnostic_events)

    def on_fill(self, fill: Fill) -> None:
        if fill.reason != OVERNIGHT_DISPLACEMENT_REVERSAL_REASON:
            return
        self._position_side = (
            DisplacementSide.LONG.value
            if fill.side == "buy"
            else DisplacementSide.SHORT.value
        )
        self._entry_pending = False
        self._entry_pending_age = 0
        self._state = DisplacementState.POSITION
        self._event(
            fill.timestamp_utc,
            "filled",
            price=fill.price,
            quantity=fill.quantity,
            reason=fill.reason,
        )

    def on_trade_closed(self, trade: Trade) -> None:
        if self._position_side is None and trade.exit_reason != (
            f"{ODR_BRANCH}_time_exit"
        ):
            return
        self._event(
            trade.exit_timestamp_utc,
            "trade_closed",
            net_pnl=trade.net_pnl,
            exit_reason=trade.exit_reason,
            mfe_points=trade.mfe_points,
            mae_points=trade.mae_points,
        )
        self._position_side = None
        self._entry_pending = False
        self._entry_pending_age = 0
        self._exit_pending = False
        self._state = DisplacementState.DONE

    def _reset_session_state(self) -> None:
        self._roll_transition = False
        self._overnight_offsets: list[int] = []
        self._overnight_closes: list[float] = []
        self._overnight_all_finite = True
        self._overnight_total_volume = 0.0
        self._overnight_pv = 0.0
        self._overnight_high: Optional[float] = None
        self._overnight_low: Optional[float] = None
        self._overnight_close: Optional[float] = None
        self._rth_started = False
        self._rth_minutes: list[int] = []
        self._rth_minute_set: set[int] = set()
        self._rth_all_finite = True
        self._rth_high: Optional[float] = None
        self._rth_low: Optional[float] = None
        self._rth_close: Optional[float] = None
        self._rth_open: Optional[float] = None
        self._highest_rth_high: Optional[float] = None
        self._lowest_rth_low: Optional[float] = None
        self._last_active_rth_minute: Optional[int] = None
        self._last_session_timestamp: Optional[str] = None
        self._features: Optional[OvernightDisplacementFeatures] = None
        self._classification: Optional[OvernightDisplacementClassification] = None
        self._state = DisplacementState.OBSERVE_OVERNIGHT
        self._entry_attempted = False
        self._extension_armed = False
        self._extension_points = 0.0

    def _event(self, timestamp_utc: str, event: str, **metadata: Any) -> None:
        classification = self._classification
        side = (
            classification.side.value
            if classification is not None
            else DisplacementSide.NONE.value
        )
        gap_direction = (
            classification.gap_direction if classification is not None else None
        )
        setup_id = (
            classification.setup_id
            if classification is not None
            else _setup_id(self._session_date or "unknown", DisplacementSide.NONE)
        )
        self._diagnostic_events.append(
            OvernightDisplacementDiagnosticEvent(
                session_date=self._session_date or "unknown",
                timestamp_utc=timestamp_utc,
                event=event,
                setup_id=setup_id,
                branch=ODR_BRANCH,
                gap_direction=gap_direction,
                side=side,
                state=self._state.value,
                metadata=dict(metadata),
            )
        )

    @staticmethod
    def _bar_is_finite(bar: MarketBar) -> bool:
        return all(
            math.isfinite(float(value))
            for value in (bar.open, bar.high, bar.low, bar.close, bar.volume)
        )

    @staticmethod
    def _overnight_offset(minute: int) -> Optional[int]:
        if minute >= 18 * 60:
            return minute - 18 * 60
        if minute <= 9 * 60 + 29:
            return 6 * 60 + minute
        return None

    def _record_overnight_bar(self, bar: MarketBar, minute: int) -> None:
        offset = self._overnight_offset(minute)
        if offset is None:
            return
        if self._overnight_offsets and offset <= self._overnight_offsets[-1]:
            return
        self._overnight_offsets.append(offset)
        if not self._bar_is_finite(bar):
            self._overnight_all_finite = False
            return
        self._overnight_closes.append(float(bar.close))
        volume = float(bar.volume)
        typical = (bar.high + bar.low + bar.close) / 3.0
        self._overnight_total_volume += volume
        self._overnight_pv += typical * volume
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
        self._overnight_close = bar.close

    def _record_rth_bar(self, bar: MarketBar, minute: int) -> None:
        self._rth_started = True
        if minute not in self._rth_minute_set:
            self._rth_minute_set.add(minute)
            self._rth_minutes.append(minute)
        if not self._bar_is_finite(bar):
            self._rth_all_finite = False
            return
        self._rth_high = bar.high if self._rth_high is None else max(self._rth_high, bar.high)
        self._rth_low = bar.low if self._rth_low is None else min(self._rth_low, bar.low)
        self._rth_close = bar.close
        self._highest_rth_high = (
            bar.high
            if self._highest_rth_high is None
            else max(self._highest_rth_high, bar.high)
        )
        self._lowest_rth_low = (
            bar.low if self._lowest_rth_low is None else min(self._lowest_rth_low, bar.low)
        )

    def _finalize_session(self) -> None:
        if self._session_date is None:
            return
        session_day = date.fromisoformat(self._session_date)
        close_minute = rth_close_minutes_et(session_day)
        # A calendar full closure (or weekend label) has no expected RTH to
        # complete.  It must not erase the last complete RTH reference or
        # advance contract identity as though an incomplete session occurred.
        if close_minute is None:
            return
        expected = (
            tuple(range(self.config.rth_open_minutes_et, close_minute))
            if close_minute is not None
            else ()
        )
        complete = bool(
            expected
            and tuple(self._rth_minutes) == expected
            and self._rth_all_finite
            and self._rth_high is not None
            and self._rth_low is not None
            and self._rth_close is not None
        )

        if self._classification is not None and self._state in (
            DisplacementState.WAIT_EXTENSION,
            DisplacementState.WAIT_REJECTION,
        ):
            self._state = DisplacementState.DONE
            self._event(
                self._last_session_timestamp or f"{self._session_date}T00:00:00Z",
                "entry_cancelled",
                reason="active_window_incomplete",
            )

        old_prior_close = self._prior_rth_close
        old_prior_complete = self._prior_rth_complete
        if (
            complete
            and old_prior_complete
            and old_prior_close is not None
            and not self._roll_transition
        ):
            true_range = max(
                float(self._rth_high) - float(self._rth_low),
                abs(float(self._rth_high) - old_prior_close),
                abs(float(self._rth_low) - old_prior_close),
            )
            if math.isfinite(true_range) and true_range > 0:
                self._dtr_history.append(true_range)
                self._dtr_session_dates.append(self._session_date)

        self._prior_rth_session_date = self._session_date
        self._prior_rth_complete = complete
        self._prior_rth_all_finite = self._rth_all_finite
        self._prior_rth_expected_minutes = len(expected)
        self._prior_rth_observed_minutes = len(self._rth_minutes)
        self._prior_rth_close = float(self._rth_close) if complete else None
        self._previous_rth_contract = self._current_contract

    def _start_session(self, session_date: date) -> None:
        self._finalize_session()
        previous_contract = self._previous_rth_contract
        self._session_date = session_date.isoformat()
        self._current_contract = front_contract_for_session(
            session_date, root=self.config.contract_root
        )
        self._reset_session_state()
        self._roll_transition = bool(
            previous_contract is not None and previous_contract != self._current_contract
        )
        self._entry_pending = False
        self._entry_pending_age = 0
        self._exit_pending = False

    def _build_features(
        self, timestamp_utc: str, *, current_rth_session: bool
    ) -> OvernightDisplacementFeatures:
        max_gap: Optional[int] = None
        if len(self._overnight_offsets) >= 2:
            max_gap = max(
                current - previous
                for previous, current in zip(
                    self._overnight_offsets, self._overnight_offsets[1:]
                )
            )
        complete_overnight = bool(
            len(self._overnight_offsets) >= 2
            and self._overnight_offsets[0]
            <= self.config.overnight_start_tolerance_minutes
            and self._overnight_offsets[-1]
            >= 929 - self.config.overnight_preopen_tolerance_minutes
            and max_gap is not None
            and max_gap <= self.config.overnight_max_gap_minutes
            and self._overnight_all_finite
            and len(self._overnight_closes) == len(self._overnight_offsets)
            and math.isfinite(self._overnight_total_volume)
            and self._overnight_total_volume > 0
        )
        overnight_vwap = (
            self._overnight_pv / self._overnight_total_volume
            if complete_overnight
            else None
        )
        overnight_range = (
            self._overnight_high - self._overnight_low
            if self._overnight_high is not None and self._overnight_low is not None
            else None
        )
        dtr20 = (
            statistics.median(self._dtr_history)
            if len(self._dtr_history) == self.config.daily_range_lookback_sessions
            else None
        )
        side = _correction_side(self._rth_open, self._prior_rth_close)
        setup_id = _setup_id(self._session_date or "unknown", side)
        gap_signed: Optional[float] = None
        gap_dtr: Optional[float] = None
        gap_direction: Optional[str] = None
        above: Optional[float] = None
        below: Optional[float] = None
        breadth: Optional[float] = None
        if (
            self._rth_open is not None
            and self._prior_rth_close is not None
            and math.isfinite(self._rth_open)
            and math.isfinite(self._prior_rth_close)
        ):
            gap_signed = self._rth_open - self._prior_rth_close
            if gap_signed > 0:
                gap_direction = "up"
            elif gap_signed < 0:
                gap_direction = "down"
        if dtr20 is not None and dtr20 > 0 and gap_signed is not None:
            gap_dtr = abs(gap_signed) / dtr20
        if self._prior_rth_close is not None and self._overnight_offsets:
            count = len(self._overnight_offsets)
            above = sum(close > self._prior_rth_close for close in self._overnight_closes) / count
            below = sum(close < self._prior_rth_close for close in self._overnight_closes) / count
            if gap_direction == "up":
                breadth = above
            elif gap_direction == "down":
                breadth = below

        return OvernightDisplacementFeatures(
            session_date=self._session_date or "unknown",
            classification_timestamp_utc=timestamp_utc,
            setup_id=setup_id,
            current_rth_session=current_rth_session,
            prior_rth_session_date=self._prior_rth_session_date,
            prior_rth_contract=self._previous_rth_contract,
            current_contract=self._current_contract,
            prior_rth_complete=self._prior_rth_complete,
            prior_rth_all_finite=self._prior_rth_all_finite,
            prior_rth_expected_minutes=self._prior_rth_expected_minutes,
            prior_rth_observed_minutes=self._prior_rth_observed_minutes,
            roll_transition=self._roll_transition,
            complete_overnight=complete_overnight,
            overnight_bar_count=len(self._overnight_offsets),
            overnight_first_offset_minutes=(
                self._overnight_offsets[0] if self._overnight_offsets else None
            ),
            overnight_last_offset_minutes=(
                self._overnight_offsets[-1] if self._overnight_offsets else None
            ),
            overnight_max_gap_minutes=max_gap,
            overnight_all_finite=self._overnight_all_finite,
            overnight_total_volume=self._overnight_total_volume,
            overnight_high=self._overnight_high,
            overnight_low=self._overnight_low,
            overnight_close=self._overnight_close,
            overnight_range=overnight_range,
            overnight_vwap=overnight_vwap,
            prior_rth_close=self._prior_rth_close,
            dtr20=dtr20,
            dtr_session_dates=tuple(self._dtr_session_dates),
            dtr_values=tuple(self._dtr_history),
            rth_open=self._rth_open,
            gap_signed_points=gap_signed,
            gap_dtr=gap_dtr,
            gap_direction=gap_direction,
            breadth_above_prior_close=above,
            breadth_below_prior_close=below,
            displacement_breadth=breadth,
        )

    def _freeze_classification(
        self, timestamp_utc: str, *, current_rth_session: bool
    ) -> OvernightDisplacementClassification:
        if self._classification is not None:
            return self._classification
        features = self._build_features(
            timestamp_utc, current_rth_session=current_rth_session
        )
        classification = classify_overnight_displacement_reversal(features, self.config)
        self._features = features
        self._classification = classification
        self._session_snapshots.append(
            OvernightDisplacementSessionSnapshot(features, classification)
        )
        self._state = (
            DisplacementState.WAIT_EXTENSION
            if classification.regime == DisplacementRegime.ELIGIBLE_GAP
            else DisplacementState.DONE
        )
        self._event(
            timestamp_utc,
            "classified",
            reason=classification.reason,
            prior_rth_complete=features.prior_rth_complete,
            complete_overnight=features.complete_overnight,
            roll_transition=features.roll_transition,
            gap_dtr=features.gap_dtr,
            displacement_breadth=features.displacement_breadth,
        )
        return classification

    def _round_down(self, price: float) -> float:
        tick = self.config.tick_size
        return round(math.floor((price + 1e-12) / tick) * tick, 10)

    def _round_up(self, price: float) -> float:
        tick = self.config.tick_size
        return round(math.ceil((price - 1e-12) / tick) * tick, 10)

    def _cancel(self, bar: MarketBar, reason: str, **metadata: Any) -> None:
        self._entry_attempted = True
        self._state = DisplacementState.DONE
        self._event(bar.timestamp_utc, "entry_cancelled", reason=reason, **metadata)

    def _reject(
        self, bar: MarketBar, reason: str, **metadata: Any
    ) -> StrategyResult:
        classification = self._classification
        self._entry_attempted = True
        self._state = DisplacementState.DONE
        payload = {
            "setup_id": classification.setup_id if classification else None,
            "branch": ODR_BRANCH,
            "gap_direction": classification.gap_direction if classification else None,
            **metadata,
        }
        self._event(bar.timestamp_utc, "entry_rejected", reason=reason, **payload)
        return StrategyResult(
            signal=SignalDecision.rejected(
                timestamp_utc=bar.timestamp_utc,
                symbol=bar.symbol,
                side=classification.side.value if classification else None,
                reason=reason,
                metadata=payload,
            )
        )

    def _accepted_order(
        self,
        bar: MarketBar,
        *,
        stop_price: float,
        target_price: float,
        close_location: float,
        structural_extreme: float,
        risk_points: float,
    ) -> StrategyResult:
        classification = self._classification
        features = self._features
        assert classification is not None and features is not None
        assert features.prior_rth_close is not None
        assert features.rth_open is not None
        assert features.dtr20 is not None
        side = classification.side
        intent_side = "buy" if side == DisplacementSide.LONG else "sell"
        reward_points = abs(target_price - bar.close)
        cross_points = abs(bar.close - features.rth_open)
        metadata = {
            "setup_id": classification.setup_id,
            "branch": ODR_BRANCH,
            "gap_direction": classification.gap_direction,
            "correction_side": side.value,
            "signal_price": bar.close,
            "stop_price": stop_price,
            "target_price": target_price,
            "prior_rth_close": features.prior_rth_close,
            "rth_open": features.rth_open,
            "dtr20": features.dtr20,
            "gap_signed_points": features.gap_signed_points,
            "gap_dtr": features.gap_dtr,
            "displacement_breadth": features.displacement_breadth,
            "extension_magnitude_points": self._extension_points,
            "extension_magnitude_dtr": self._extension_points / features.dtr20,
            "decisive_cross_distance_points": cross_points,
            "decisive_cross_distance_dtr": cross_points / features.dtr20,
            "close_location": close_location,
            "structural_extreme": structural_extreme,
            "decision_risk_points": risk_points,
            "decision_risk_dtr": risk_points / features.dtr20,
            "target_distance_points": reward_points,
            "target_distance_r": reward_points / risk_points,
        }
        self._entry_attempted = True
        self._entry_pending = True
        self._entry_pending_age = 0
        self._state = DisplacementState.ENTRY_PENDING
        self._event(
            bar.timestamp_utc,
            "entry_confirmed",
            reason=OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
            **metadata,
        )
        signal = SignalDecision.accepted(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=side.value,
            reason=OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
            metadata=metadata,
        )
        intent = OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side=intent_side,
            quantity=self.config.contracts,
            reason=OVERNIGHT_DISPLACEMENT_REVERSAL_REASON,
            metadata=metadata,
        )
        return StrategyResult(signal=signal, order_intents=(intent,))

    def _process_active_bar(self, bar: MarketBar, minute: int) -> StrategyResult:
        classification = self._classification
        features = self._features
        if classification is None or features is None:
            return StrategyResult()
        if classification.regime != DisplacementRegime.ELIGIBLE_GAP:
            return StrategyResult()
        if self._state not in (
            DisplacementState.WAIT_EXTENSION,
            DisplacementState.WAIT_REJECTION,
        ):
            return StrategyResult()
        assert features.prior_rth_close is not None
        assert features.rth_open is not None
        assert features.dtr20 is not None
        assert self._highest_rth_high is not None and self._lowest_rth_low is not None

        if self._last_active_rth_minute is not None and minute != (
            self._last_active_rth_minute + 1
        ):
            self._cancel(
                bar,
                "active_rth_minute_gap",
                previous_minute=self._last_active_rth_minute,
                current_minute=minute,
            )
            return StrategyResult()
        self._last_active_rth_minute = minute

        side = classification.side
        prior_close = features.prior_rth_close
        if (
            side == DisplacementSide.SHORT and bar.low <= prior_close
        ) or (
            side == DisplacementSide.LONG and bar.high >= prior_close
        ):
            self._cancel(
                bar,
                "correction_objective_touched_before_entry",
                prior_rth_close=prior_close,
            )
            return StrategyResult()

        if side == DisplacementSide.SHORT:
            extension = self._highest_rth_high - features.rth_open
        else:
            extension = features.rth_open - self._lowest_rth_low
        self._extension_points = max(self._extension_points, extension)
        if (
            not self._extension_armed
            and self._extension_points >= self.config.extension_dtr * features.dtr20
        ):
            self._extension_armed = True
            self._state = DisplacementState.WAIT_REJECTION
            self._event(
                bar.timestamp_utc,
                "extension_armed",
                extension_magnitude_points=self._extension_points,
                extension_magnitude_dtr=self._extension_points / features.dtr20,
            )

        gap_sign = 1.0 if classification.gap_direction == "up" else -1.0
        decisive = gap_sign * (bar.close - features.rth_open) <= (
            -self.config.rejection_margin_dtr * features.dtr20
        )
        if not decisive:
            return StrategyResult()
        if not self._extension_armed:
            self._cancel(bar, "decisive_rejection_before_extension")
            return StrategyResult()

        bar_range = bar.high - bar.low
        close_location = (
            (bar.close - bar.low) / bar_range
            if side == DisplacementSide.LONG and bar_range > 0
            else (bar.high - bar.close) / bar_range
            if side == DisplacementSide.SHORT and bar_range > 0
            else None
        )
        if close_location is None or close_location < (
            self.config.correction_close_location_min
        ):
            self._cancel(
                bar,
                "decisive_rejection_close_location_failed",
                close_location=close_location,
            )
            return StrategyResult()

        if side == DisplacementSide.LONG:
            structural_extreme = self._lowest_rth_low
            stop = self._round_down(
                structural_extreme - self.config.stop_buffer_dtr * features.dtr20
            )
            risk = bar.close - stop
            objective_distance = prior_close - bar.close
        else:
            structural_extreme = self._highest_rth_high
            stop = self._round_up(
                structural_extreme + self.config.stop_buffer_dtr * features.dtr20
            )
            risk = stop - bar.close
            objective_distance = bar.close - prior_close

        risk_dtr = risk / features.dtr20
        if not self.config.min_risk_dtr <= risk_dtr <= self.config.max_risk_dtr:
            return self._reject(
                bar,
                "decision_risk_geometry",
                decision_risk_points=risk,
                decision_risk_dtr=risk_dtr,
            )
        if objective_distance <= 0:
            return self._reject(
                bar,
                "prior_close_not_in_profitable_direction",
                objective_distance=objective_distance,
            )

        target_distance = min(objective_distance, self.config.max_target_r * risk)
        if side == DisplacementSide.LONG:
            target = self._round_down(bar.close + target_distance)
            rounded_reward = target - bar.close
        else:
            target = self._round_up(bar.close - target_distance)
            rounded_reward = bar.close - target
        reward_r = rounded_reward / risk
        if rounded_reward <= 0 or reward_r < self.config.min_reward_r:
            return self._reject(
                bar,
                "decision_reward_geometry",
                target_distance_points=rounded_reward,
                target_distance_r=reward_r,
            )
        return self._accepted_order(
            bar,
            stop_price=stop,
            target_price=target,
            close_location=close_location,
            structural_extreme=structural_extreme,
            risk_points=risk,
        )

    def _hard_exit(self, bar: MarketBar, minute: int) -> tuple[ExitDecision, ...]:
        signal_minute = self.config.hard_exit_fill_minutes_et - 1
        if (
            self._position_side is not None
            and not self._exit_pending
            and minute == signal_minute
        ):
            self._exit_pending = True
            self._event(bar.timestamp_utc, "time_exit_signalled")
            return (
                ExitDecision(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    reason=f"{ODR_BRANCH}_time_exit",
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
                self._state = DisplacementState.DONE
                self._event(bar.timestamp_utc, "entry_pending_expired")

        minute = session.minutes_from_midnight_et
        exits = self._hard_exit(bar, minute)
        if not session.is_rth:
            if not self._rth_started:
                self._record_overnight_bar(bar, minute)
            return StrategyResult(exits=exits)

        self._record_rth_bar(bar, minute)
        if self._classification is None:
            if minute == self.config.rth_open_minutes_et:
                self._rth_open = bar.open
            self._freeze_classification(
                bar.timestamp_utc,
                current_rth_session=session.rth_close_minutes_et is not None,
            )

        result = StrategyResult(exits=exits)
        available = bool(
            self._classification is not None
            and self._classification.regime == DisplacementRegime.ELIGIBLE_GAP
            and self._position_side is None
            and not self._entry_pending
            and not self._entry_attempted
        )
        if available and self.config.rth_open_minutes_et <= minute < (
            self.config.entry_end_minutes_et
        ):
            if not self._bar_is_finite(bar):
                self._cancel(bar, "nonfinite_active_rth_bar")
                return StrategyResult(exits=exits)
            order = self._process_active_bar(bar, minute)
            if order.signal is not None or order.order_intents:
                return StrategyResult(
                    signal=order.signal,
                    order_intents=order.order_intents,
                    risk_vetoes=order.risk_vetoes,
                    stop_updates=order.stop_updates,
                    exits=exits,
                )
            if (
                minute == self.config.entry_end_minutes_et - 1
                and self._state in (
                    DisplacementState.WAIT_EXTENSION,
                    DisplacementState.WAIT_REJECTION,
                )
            ):
                self._cancel(bar, "entry_window_expired")
        elif available and minute >= self.config.entry_end_minutes_et:
            if (
                self._last_active_rth_minute is not None
                and self._last_active_rth_minute < self.config.entry_end_minutes_et - 1
            ):
                self._cancel(
                    bar,
                    "active_rth_minute_gap",
                    previous_minute=self._last_active_rth_minute,
                    current_minute=minute,
                )
            else:
                self._cancel(bar, "entry_window_expired")
        return result
