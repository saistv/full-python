from __future__ import annotations

import json

from full_python.live.risk_probe import PROBE_ENDPOINTS, run_risk_probe
from full_python.tradovate.errors import TradovateRequestError


class GetOnlyHttp:
    """Fake http client with NO post attribute: any POST would AttributeError."""

    def __init__(self, failures=()):
        self.gets = []
        self._failures = set(failures)

    def get(self, path):
        self.gets.append(path)
        if path in self._failures:
            raise TradovateRequestError("Tradovate request failed with status 404")
        return [{"id": 1, "name": "DEMO123", "accessToken": "sekret"}]


def test_probe_gets_every_endpoint_and_writes_snapshot(tmp_path) -> None:
    http = GetOnlyHttp()
    out = tmp_path / "session" / "account_risk.json"

    snapshot = run_risk_probe(http, out)

    assert http.gets == list(PROBE_ENDPOINTS)
    assert "/userAccountAutoLiq/list" in PROBE_ENDPOINTS  # the DLL evidence
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert set(on_disk) == set(PROBE_ENDPOINTS)
    assert on_disk == snapshot


def test_probe_records_endpoint_failures_without_dying(tmp_path) -> None:
    http = GetOnlyHttp(failures={"/marginSnapshot/list"})
    out = tmp_path / "account_risk.json"

    snapshot = run_risk_probe(http, out)

    assert "error" in snapshot["/marginSnapshot/list"]
    assert isinstance(snapshot["/account/list"], list)


def test_probe_redacts_sensitive_keys(tmp_path) -> None:
    out = tmp_path / "account_risk.json"
    snapshot = run_risk_probe(GetOnlyHttp(), out)
    assert snapshot["/account/list"][0]["accessToken"] == "<redacted>"
    assert "sekret" not in out.read_text(encoding="utf-8")
