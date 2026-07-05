from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class VwapReversionConfig:
    """MR variant 1 — VWAP reversion v0.2-py.

    Defaults are the literature-faithful baseline mandated by the MR
    research contract (all eight design principles; see
    docs/research/2026-07-03-mr-vwap-v02-run1-hypothesis.md). Sweeps come
    after the baseline verdict, on axes chosen from its failure mode.
    """

    name: str = "vwap_reversion_v02"
    atr_length: int = 14
    # Extension unit (run 2 fix): "vwap_sigma" = volume-weighted stdev of
    # typical price around session VWAP (the standard VWAP-band sigma,
    # self-calibrating per day). "atr" = run 1's mis-scaled unit, kept only
    # to reproduce that run.
    band_mode: str = "vwap_sigma"
    band_atr_mult: float = 2.5  # principle 6: extreme entry, 2.5-3 sigma
    stop_atr_mult: float = 1.0  # principle 3: tight stop
    rr_multiple: float = 2.0  # principle 2: R:R >= 2:1, static target
    time_stop_bars: int = 20  # principle 4: short hold
    adx_length: int = 14
    adx_max: float = 20.0  # principle 5: strict regime gate
    entry_start_minutes_et: int = 10 * 60  # avoid AT's 9:30-10:00 window
    entry_end_minutes_et: int = 15 * 60 + 30
    cooldown_bars: int = 5
    contracts: int = 1
    tick_size: float = 0.25
    warmup_bars: int = 100

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
