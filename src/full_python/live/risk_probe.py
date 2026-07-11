"""Read-only account risk snapshot (GET only -- never a POST).

Captures the demo account's platform-side risk configuration at session
start. /userAccountAutoLiq/list is the direct evidence for the open
operational question: does Tradovate/the prop firm enforce an
account-level daily-loss limit, and does it force-flatten or only block
new orders (parent adapter spec, Open Operational Decisions). This
module records; interpretation happens in the order-test spec.
"""
from __future__ import annotations

import json
from pathlib import Path

from full_python.tradovate.errors import TradovateError
from full_python.tradovate.http import _redact

PROBE_ENDPOINTS = (
    "/account/list",
    "/cashBalance/list",
    "/userAccountAutoLiq/list",
    "/marginSnapshot/list",
)


def run_risk_probe(http, out_path) -> dict:
    snapshot = {}
    for endpoint in PROBE_ENDPOINTS:
        try:
            snapshot[endpoint] = _redact(http.get(endpoint))
        except TradovateError as exc:
            snapshot[endpoint] = {"error": str(exc)}
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    return snapshot
