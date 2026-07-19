"""Frozen configuration for Opening Auction Regime v1.

The defaults are preregistered in
``docs/research/2026-07-17-opening-auction-regime-v1-hypothesis.md``.
They are deliberately flat and explicit so reports, hashes, and any later
single-axis robustness runs can identify every degree of freedom.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math


@dataclass(frozen=True)
class OpeningAuctionRegimeConfig:
    name: str = "opening_auction_regime_v1"
    contract_root: str = "NQ"
    tick_size: float = 0.25
    contracts: int = 1

    observation_start_minutes_et: int = 9 * 60 + 30
    observation_minutes: int = 15
    continuation_entry_end_minutes_et: int = 10 * 60 + 15
    hard_exit_fill_minutes_et: int = 11 * 60 + 30

    daily_range_lookback_sessions: int = 20
    opening_volume_lookback_sessions: int = 20
    overnight_start_tolerance_minutes: int = 5
    overnight_preopen_tolerance_minutes: int = 5
    overnight_max_gap_minutes: int = 15

    opening_volume_ratio_min: float = 1.00

    initiative_displacement_dtr: float = 0.15
    initiative_efficiency_min: float = 0.55
    initiative_close_location_min: float = 0.80
    initiative_vwap_acceptance_fraction: float = 0.80
    initiative_external_break_dtr: float = 0.05

    continuation_pullback_fraction: float = 0.25
    continuation_stop_buffer_dtr: float = 0.05
    continuation_min_risk_dtr: float = 0.08
    continuation_max_risk_dtr: float = 0.30
    continuation_reward_r: float = 3.00

    failure_breach_dtr: float = 0.10
    failure_reclaim_dtr: float = 0.05
    failure_close_location_min: float = 0.65
    failure_vwap_reclaim_bars: int = 3
    failure_stop_buffer_dtr: float = 0.05
    failure_min_risk_dtr: float = 0.08
    failure_max_risk_dtr: float = 0.30
    failure_min_reward_r: float = 1.50
    failure_max_reward_r: float = 3.00

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name is required")
        if not isinstance(self.contract_root, str) or not self.contract_root.strip():
            raise ValueError("contract_root is required")
        integer_fields = (
            "contracts",
            "observation_start_minutes_et",
            "observation_minutes",
            "continuation_entry_end_minutes_et",
            "hard_exit_fill_minutes_et",
            "daily_range_lookback_sessions",
            "opening_volume_lookback_sessions",
            "overnight_start_tolerance_minutes",
            "overnight_preopen_tolerance_minutes",
            "overnight_max_gap_minutes",
            "failure_vwap_reclaim_bars",
        )
        for field_name in integer_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} must be an integer")
        if not math.isfinite(self.tick_size) or self.tick_size <= 0:
            raise ValueError("tick_size must be finite and positive")
        if self.contracts < 1:
            raise ValueError("contracts must be at least 1")
        if not 0 <= self.observation_start_minutes_et < 24 * 60:
            raise ValueError("observation_start_minutes_et is outside the day")
        if self.observation_minutes < 5:
            raise ValueError("observation_minutes must be at least 5")
        if self.observation_end_minutes_et > 24 * 60:
            raise ValueError("opening observation extends beyond the day")
        if self.continuation_entry_end_minutes_et <= self.observation_end_minutes_et:
            raise ValueError("continuation window must follow the observation window")
        if self.hard_exit_fill_minutes_et <= self.continuation_entry_end_minutes_et:
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

        positive_fields = (
            "opening_volume_ratio_min",
            "initiative_displacement_dtr",
            "initiative_efficiency_min",
            "initiative_close_location_min",
            "initiative_external_break_dtr",
            "continuation_pullback_fraction",
            "continuation_stop_buffer_dtr",
            "continuation_min_risk_dtr",
            "continuation_max_risk_dtr",
            "continuation_reward_r",
            "failure_breach_dtr",
            "failure_reclaim_dtr",
            "failure_close_location_min",
            "failure_stop_buffer_dtr",
            "failure_min_risk_dtr",
            "failure_max_risk_dtr",
            "failure_min_reward_r",
            "failure_max_reward_r",
        )
        for field_name in positive_fields:
            value = getattr(self, field_name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field_name} must be finite and positive")

        fraction_fields = (
            "initiative_efficiency_min",
            "initiative_close_location_min",
            "initiative_vwap_acceptance_fraction",
            "continuation_pullback_fraction",
            "failure_close_location_min",
        )
        for field_name in fraction_fields:
            value = getattr(self, field_name)
            if not 0 < value <= 1:
                raise ValueError(f"{field_name} must be in (0, 1]")
        if self.failure_vwap_reclaim_bars < 1:
            raise ValueError("failure_vwap_reclaim_bars must be positive")
        if self.failure_vwap_reclaim_bars > self.observation_minutes:
            raise ValueError("failure VWAP bars cannot exceed observation bars")
        if self.continuation_min_risk_dtr >= self.continuation_max_risk_dtr:
            raise ValueError("continuation risk bounds are reversed")
        if self.failure_min_risk_dtr >= self.failure_max_risk_dtr:
            raise ValueError("failure risk bounds are reversed")
        if self.failure_min_reward_r > self.failure_max_reward_r:
            raise ValueError("failure reward bounds are reversed")

    @property
    def observation_end_minutes_et(self) -> int:
        return self.observation_start_minutes_et + self.observation_minutes

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
