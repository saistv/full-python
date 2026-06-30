from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class BaselineMomentumConfig:
    name: str = "baseline_momentum_breakout"
    instrument_for_signal: str = "NQ"
    instrument_for_risk: str = "MNQ"
    promote_session: str = "RTH"
    max_drawdown_dollars: float = 5000.0
    contract_multiplier: float = 2.0
    commission_per_contract: float = 1.0
    slippage_points: float = 1.0
    breakout_lookback_bars: int = 2
    stop_points: float = 30.0
    min_body_points: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
