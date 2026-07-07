"""MR variant 2 -- opening range fade v1 config.

Literature-faithful per the MR research contract: ATR bracket with a
static 2:1 target, 1-ATR frozen stop, 20-bar time stop, daily ADX(14)<20
gate, 10:00-15:30 ET entry window (disjoint from Adaptive Trend). The
signal fades a FAILED 9:30-10:00 opening-range breakout (extension >= 1
ATR beyond the edge, then a close back inside within the failure window).
Hypothesis: docs/research/2026-07-06-mr-orfade-run1-hypothesis.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class OpeningRangeFadeConfig:
    name: str = "opening_range_fade_v1"
    atr_length: int = 14
    or_start_minutes_et: int = 9 * 60 + 30   # 9:30
    or_end_minutes_et: int = 10 * 60          # 10:00 (OR frozen here)
    entry_start_minutes_et: int = 10 * 60     # 10:00, disjoint from AT
    entry_end_minutes_et: int = 15 * 60 + 30  # 15:30
    breakout_atr_mult: float = 1.0
    failure_window_bars: int = 10
    stop_atr_mult: float = 1.0
    rr_multiple: float = 2.0
    time_stop_bars: int = 20
    adx_length: int = 14
    adx_max: float = 20.0
    cooldown_bars: int = 5
    contracts: int = 1
    tick_size: float = 0.25
    warmup_bars: int = 100

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
