from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class DataManifest:
    dataset_name: str
    source: str
    symbol: str
    contract: str
    timezone: str
    session: str
    start_timestamp_utc: str
    end_timestamp_utc: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def stable_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
