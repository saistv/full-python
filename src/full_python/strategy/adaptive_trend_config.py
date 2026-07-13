from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class AdaptiveTrendConfig:
    """TradingView-reconciled signal calibration.

    The old $251K / PF 2.071 TradingView headline is a retired historical
    artifact. Python reports and the pre-registered Gate 1 protocol are the
    performance and promotion authority.
    """

    name: str = "adaptive_trend_v66_flat"
    atf_length: int = 12
    atf_smooth: int = 22
    atf_sensitivity: float = 4.5
    sr_left_bars: int = 5
    sr_right_bars: int = 3
    sr_break_lookback: int = 2
    signal_valid_bars: int = 2
    prove_it_bars: int = 2
    wings_body_atr_frac: float = 0.40
    wings_close_frac: float = 0.65
    wings_atr_length: int = 14
    sqz_bb_length: int = 20
    sqz_bb_mult: float = 2.0
    sqz_kc_length: int = 20
    sqz_kc_mult: float = 1.5
    # Explicit component switches exist for registered diagnostic ablations.
    # Production keeps every switch ON; they are not optimization parameters.
    enable_squeeze_momentum_gate: bool = True
    enable_squeeze_release_gate: bool = True
    enable_wings_gate: bool = True
    enable_prove_it_hold: bool = True
    ma_50_length: int = 50
    ma_200_length: int = 200
    sr_stop_buffer: float = 5.0
    sr_min_stop_distance: float = 15.0
    max_stop_distance: float = 31.0
    fallback_stop_points: float = 30.0
    entry_start_minutes_et: int = 9 * 60 + 30
    entry_end_minutes_et: int = 10 * 60
    stop_loss_cooldown_bars: int = 7
    breakeven_exit_cooldown_bars: int = 1
    entry_cooldown_bars: int = 3
    contracts: int = 1
    tick_size: float = 0.25
    warmup_bars: int = 200
    # M2b sizing layer (defaults OFF = the reconciled flat parity config).
    # Production runs both ON: AM 1->4 with Any Non-Win reset, DLL $1,000.
    enable_anti_martingale: bool = False
    max_contracts_per_entry: int = 4
    enable_daily_loss_limit: bool = False
    daily_loss_limit: float = 1000.0
    enable_projected_risk_dll_guard: bool = True
    dll_risk_buffer: float = 0.0
    dollar_point_value: float = 20.0  # must match the engine's point_value
    enable_prior_vol_gate: bool = False
    # Train-calibrated high-tercile boundary of prior_realized_vol (stdev
    # of log returns over the PRIOR completed RTH session's 1-minute
    # closes, >=30 observations required). Derived from
    # full_python.regime._tercile_bounds over ONLY the Gate 1 train
    # window (2023-01-01 -> 2025-06-30, 642 sessions with enough prior
    # data) -- see docs/decisions/2026-07-05-gate1-phase2-diagnosis.md.
    # Fixed deliberately, not recomputed dynamically, to avoid lookahead
    # into holdout/live data. Re-derive only if the train window itself
    # is redefined.
    prior_vol_high_threshold: float = 0.0004638315483775433

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def production_am_config() -> AdaptiveTrendConfig:
    """The validated production sizing stack on top of the parity core."""
    return AdaptiveTrendConfig(
        name="adaptive_trend_v66_am",
        enable_anti_martingale=True,
        enable_daily_loss_limit=True,
    )
