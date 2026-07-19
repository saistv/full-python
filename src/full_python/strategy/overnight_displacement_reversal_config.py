"""Frozen configuration for Overnight Displacement Reversal v3."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math


@dataclass(frozen=True)
class OvernightDisplacementReversalConfig:
    name: str = "overnight_displacement_reversal_v3"
    contract_root: str = "NQ"
    tick_size: float = 0.25
    contracts: int = 1

    rth_open_minutes_et: int = 9 * 60 + 30
    entry_end_minutes_et: int = 11 * 60
    hard_exit_fill_minutes_et: int = 12 * 60

    daily_range_lookback_sessions: int = 20
    overnight_start_tolerance_minutes: int = 5
    overnight_preopen_tolerance_minutes: int = 5
    overnight_max_gap_minutes: int = 15

    min_gap_dtr: float = 0.05
    max_gap_dtr: float = 0.75
    min_displacement_breadth: float = 0.50
    extension_dtr: float = 0.02
    rejection_margin_dtr: float = 0.01
    correction_close_location_min: float = 0.65
    stop_buffer_dtr: float = 0.02
    min_risk_dtr: float = 0.05
    max_risk_dtr: float = 0.20
    min_reward_r: float = 1.25
    max_target_r: float = 2.00

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name is required")
        if not isinstance(self.contract_root, str) or not self.contract_root.strip():
            raise ValueError("contract_root is required")

        integer_fields = (
            "contracts",
            "rth_open_minutes_et",
            "entry_end_minutes_et",
            "hard_exit_fill_minutes_et",
            "daily_range_lookback_sessions",
            "overnight_start_tolerance_minutes",
            "overnight_preopen_tolerance_minutes",
            "overnight_max_gap_minutes",
        )
        for field_name in integer_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} must be an integer")

        if self.contracts < 1:
            raise ValueError("contracts must be at least 1")
        if not 0 <= self.rth_open_minutes_et < 24 * 60:
            raise ValueError("rth_open_minutes_et is outside the day")
        if not self.rth_open_minutes_et < self.entry_end_minutes_et:
            raise ValueError("entry window must follow the RTH open")
        if not self.entry_end_minutes_et < self.hard_exit_fill_minutes_et < 18 * 60:
            raise ValueError("hard exit must follow the entry window and precede 18:00")
        if self.daily_range_lookback_sessions < 2:
            raise ValueError("daily_range_lookback_sessions must be at least 2")
        if self.overnight_start_tolerance_minutes < 0:
            raise ValueError("overnight_start_tolerance_minutes must be nonnegative")
        if self.overnight_preopen_tolerance_minutes < 0:
            raise ValueError("overnight_preopen_tolerance_minutes must be nonnegative")
        if self.overnight_max_gap_minutes < 1:
            raise ValueError("overnight_max_gap_minutes must be positive")

        positive_fields = (
            "tick_size",
            "min_gap_dtr",
            "max_gap_dtr",
            "min_displacement_breadth",
            "extension_dtr",
            "rejection_margin_dtr",
            "correction_close_location_min",
            "stop_buffer_dtr",
            "min_risk_dtr",
            "max_risk_dtr",
            "min_reward_r",
            "max_target_r",
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

        for field_name in (
            "min_displacement_breadth",
            "correction_close_location_min",
        ):
            if getattr(self, field_name) > 1:
                raise ValueError(f"{field_name} must be in (0, 1]")
        if self.min_gap_dtr >= self.max_gap_dtr:
            raise ValueError("gap bounds are reversed")
        if self.min_risk_dtr >= self.max_risk_dtr:
            raise ValueError("risk bounds are reversed")
        if self.min_reward_r > self.max_target_r:
            raise ValueError("min_reward_r cannot exceed max_target_r")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
