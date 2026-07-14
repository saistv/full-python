from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math

FILL_TIMING_NEXT_BAR_OPEN = "next_bar_open"
FILL_TIMING_SIGNAL_BAR_CLOSE = "signal_bar_close"


@dataclass(frozen=True)
class SimulationConfig:
    """Execution assumptions for the fill simulator.

    Defaults are MNQ-first and deliberately pessimistic; see
    docs/decisions/2026-07-03-fill-simulation-policy.md. ``signal_bar_close``
    exists only for reconciliation against legacy TradingView runs and must
    not be used for promotion decisions.
    """

    point_value: float = 2.0
    commission_per_contract_round_trip: float = 1.0
    entry_slippage_points: float = 1.0
    exit_slippage_points: float = 0.5
    rth_open_extra_entry_slippage_points: float = 1.0
    # Additional completed one-minute bars beyond the normal next-bar-open
    # fill. Zero preserves the production reference behavior.
    entry_delay_bars: int = 0
    # Deterministic infrastructure/missed-signal stress. This is not a market
    # order fill probability model; the intent identity and seed select misses.
    entry_fill_rate: float = 1.0
    entry_fill_seed: int = 0
    fill_timing: str = FILL_TIMING_NEXT_BAR_OPEN
    rth_entries_only: bool = True
    flatten_hour_et: int = 15
    flatten_minute_et: int = 59
    max_contracts: int = 10
    # Equity-based daily loss limit (None = off). Session P&L = realized net
    # since session start + GROSS unrealized at bar close, matching Pine's
    # strategy.equity (openprofit excludes the open trade's commission).
    # On breach: the stop is cancelled, the position flattens at next bar
    # open, and entries are vetoed for the rest of the session.
    daily_loss_limit: float | None = None

    def __post_init__(self) -> None:
        if self.fill_timing not in (FILL_TIMING_NEXT_BAR_OPEN, FILL_TIMING_SIGNAL_BAR_CLOSE):
            raise ValueError(f"Unsupported fill_timing: {self.fill_timing}")
        if not math.isfinite(self.point_value) or self.point_value <= 0:
            raise ValueError("point_value must be positive")
        for field_name in (
            "commission_per_contract_round_trip",
            "entry_slippage_points",
            "exit_slippage_points",
            "rth_open_extra_entry_slippage_points",
        ):
            value = getattr(self, field_name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{field_name} must be finite and nonnegative")
        if self.entry_delay_bars < 0:
            raise ValueError("entry_delay_bars must be nonnegative")
        if (
            not math.isfinite(self.entry_fill_rate)
            or not 0.0 <= self.entry_fill_rate <= 1.0
        ):
            raise ValueError("entry_fill_rate must be between 0 and 1")
        if (
            self.fill_timing != FILL_TIMING_NEXT_BAR_OPEN
            and (self.entry_delay_bars != 0 or self.entry_fill_rate != 1.0)
        ):
            raise ValueError(
                "entry timing controls require fill_timing='next_bar_open'"
            )
        if self.max_contracts < 1:
            raise ValueError("max_contracts must be at least 1")
        if self.daily_loss_limit is not None and (
            not math.isfinite(self.daily_loss_limit) or self.daily_loss_limit <= 0
        ):
            raise ValueError("daily_loss_limit must be finite and positive when set")

    @property
    def flatten_minutes_et(self) -> int:
        return self.flatten_hour_et * 60 + self.flatten_minute_et

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
