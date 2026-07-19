"""Frozen configuration for Opening Auction Retest v2.

The defaults are preregistered in the matching hypothesis document.  The
configuration deliberately contains no live/backtest modes, broker confirmation
switches, hidden presets, or sizing progression.  It describes one strategy only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math


@dataclass(frozen=True)
class OpeningAuctionRetestConfig:
    name: str = "opening_auction_retest_v2"
    contract_root: str = "NQ"
    tick_size: float = 0.25
    contracts: int = 1

    observation_start_minutes_et: int = 9 * 60 + 30
    observation_minutes: int = 15
    entry_end_minutes_et: int = 10 * 60 + 30
    hard_exit_fill_minutes_et: int = 11 * 60 + 30

    daily_range_lookback_sessions: int = 20
    opening_volume_lookback_sessions: int = 20
    overnight_start_tolerance_minutes: int = 5
    overnight_preopen_tolerance_minutes: int = 5
    overnight_max_gap_minutes: int = 15

    reference_cross_dtr: float = 0.02
    acceptance_margin_dtr: float = 0.01
    acceptance_lookback_bars: int = 5
    acceptance_closes_required: int = 4
    rejection_margin_dtr: float = 0.01
    rejection_lookback_bars: int = 3

    retest_tolerance_dtr: float = 0.03
    retest_hold_margin_dtr: float = 0.01
    retest_bar_close_location_min: float = 0.65
    confirmation_max_bars: int = 3
    confirmation_reference_margin_dtr: float = 0.01
    invalidation_margin_dtr: float = 0.01
    stop_buffer_dtr: float = 0.02
    min_risk_dtr: float = 0.04
    max_risk_dtr: float = 0.16
    reward_r: float = 2.50

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name is required")
        if not isinstance(self.contract_root, str) or not self.contract_root.strip():
            raise ValueError("contract_root is required")

        integer_fields = (
            "contracts",
            "observation_start_minutes_et",
            "observation_minutes",
            "entry_end_minutes_et",
            "hard_exit_fill_minutes_et",
            "daily_range_lookback_sessions",
            "opening_volume_lookback_sessions",
            "overnight_start_tolerance_minutes",
            "overnight_preopen_tolerance_minutes",
            "overnight_max_gap_minutes",
            "acceptance_lookback_bars",
            "acceptance_closes_required",
            "rejection_lookback_bars",
            "confirmation_max_bars",
        )
        for field_name in integer_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} must be an integer")

        if (
            isinstance(self.tick_size, bool)
            or not isinstance(self.tick_size, (int, float))
            or not math.isfinite(self.tick_size)
            or self.tick_size <= 0
        ):
            raise ValueError("tick_size must be finite and positive")
        if self.contracts < 1:
            raise ValueError("contracts must be at least 1")
        if not 0 <= self.observation_start_minutes_et < 24 * 60:
            raise ValueError("observation_start_minutes_et is outside the day")
        if self.observation_minutes < 5:
            raise ValueError("observation_minutes must be at least 5")
        if self.observation_end_minutes_et > 24 * 60:
            raise ValueError("opening observation extends beyond the day")
        if self.entry_end_minutes_et <= self.observation_end_minutes_et:
            raise ValueError("entry window must follow the observation window")
        if self.hard_exit_fill_minutes_et <= self.entry_end_minutes_et:
            raise ValueError("hard exit must follow the entry window")
        if self.hard_exit_fill_minutes_et >= 18 * 60:
            raise ValueError("hard exit must occur before the CME session boundary")
        if self.daily_range_lookback_sessions < 2:
            raise ValueError("daily_range_lookback_sessions must be at least 2")
        if self.opening_volume_lookback_sessions < 2:
            raise ValueError("opening_volume_lookback_sessions must be at least 2")
        if self.overnight_start_tolerance_minutes < 0:
            raise ValueError("overnight_start_tolerance_minutes must be nonnegative")
        if self.overnight_preopen_tolerance_minutes < 0:
            raise ValueError("overnight_preopen_tolerance_minutes must be nonnegative")
        if self.overnight_max_gap_minutes < 1:
            raise ValueError("overnight_max_gap_minutes must be positive")
        if not 1 <= self.acceptance_lookback_bars <= self.observation_minutes:
            raise ValueError("acceptance_lookback_bars is outside the observation")
        if not 1 <= self.acceptance_closes_required <= self.acceptance_lookback_bars:
            raise ValueError("acceptance_closes_required exceeds its lookback")
        if not 1 <= self.rejection_lookback_bars <= self.observation_minutes:
            raise ValueError("rejection_lookback_bars is outside the observation")
        if self.confirmation_max_bars < 1:
            raise ValueError("confirmation_max_bars must be positive")

        positive_fields = (
            "reference_cross_dtr",
            "acceptance_margin_dtr",
            "rejection_margin_dtr",
            "retest_tolerance_dtr",
            "retest_hold_margin_dtr",
            "retest_bar_close_location_min",
            "confirmation_reference_margin_dtr",
            "invalidation_margin_dtr",
            "stop_buffer_dtr",
            "min_risk_dtr",
            "max_risk_dtr",
            "reward_r",
        )
        for field_name in positive_fields:
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{field_name} must be finite and positive")

        fraction_fields = (
            "retest_bar_close_location_min",
        )
        for field_name in fraction_fields:
            value = getattr(self, field_name)
            if not 0 < value <= 1:
                raise ValueError(f"{field_name} must be in (0, 1]")
        if self.min_risk_dtr >= self.max_risk_dtr:
            raise ValueError("risk bounds are reversed")

    @property
    def observation_end_minutes_et(self) -> int:
        return self.observation_start_minutes_et + self.observation_minutes

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
