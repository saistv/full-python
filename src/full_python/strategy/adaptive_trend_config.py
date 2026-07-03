from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class AdaptiveTrendConfig:
    """Validated production calibration ($251K / PF 2.071 / 448 trades).

    Defaults ARE the production config. Changing production requires
    >= $275K net AND PF >= 2.071 on the same 3-year TradingView backtest.
    M2 scope is flat 1-contract parity: anti-martingale and the daily
    loss limit are not ported yet (M2b).
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

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
