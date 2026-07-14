from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from full_python.live.runner import (
    bars_until,
    build_observe_session,
    main,
    observe_adapter_config,
    run_observe_session,
)
from full_python.models import MarketBar
from full_python.tradovate.errors import TradovateOrderSafetyError, TradovateWebSocketError


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current = self.current + timedelta(**kwargs)


class FakeChartWs:
    """Implements the ChartWebSocketClient protocol with scripted events."""

    def __init__(self, events) -> None:
        self.events = list(events)
        self.requests = []
        self.closed = False

    def request(self, endpoint, payload):
        self.requests.append((endpoint, payload))
        if endpoint == "md/getChart":
            return {"historicalId": 1, "realtimeId": 2}
        return {}

    def receive_event(self, timeout_seconds):
        if self.events:
            return self.events.pop(0)
        return None

    def close(self):
        self.closed = True


def _chart_event(ts: str, price: float, symbol_unused: str = "") -> dict:
    return {"e": "chart", "d": {"charts": [{"id": 2, "bars": [{
        "timestamp": ts, "open": price, "high": price, "low": price,
        "close": price, "volume": 1,
    }]}]}}


def _eoh_event() -> dict:
    """End-of-history marker for the historical subscription (id 1)."""
    return {"e": "chart", "d": {"charts": [{"id": 1, "eoh": True}]}}


def _bar(ts: str, price: float = 100.0) -> MarketBar:
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=price, high=price,
                     low=price, close=price, volume=1.0)


def _reference_csv(path, rows) -> str:
    lines = ["timestamp,symbol,open,high,low,close,volume"]
    lines.extend(
        f"{ts},NQU6,{price},{price},{price},{price},1.0"
        for ts, price in rows
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_observe_adapter_config_pins_orders_off() -> None:
    config = observe_adapter_config("DEMO123", 456)
    assert config.order_enabled is False
    assert config.flatten_enabled is False
    assert config.environment.name == "demo"
    assert config.dollar_point_value == 20.0


def test_cli_has_no_flag_that_could_enable_orders() -> None:
    with pytest.raises(SystemExit):  # argparse rejects unknown flags
        main(["--order-enabled"])
    with pytest.raises(SystemExit):
        main(["--flatten-enabled"])
    with pytest.raises(SystemExit):
        main(["--environment", "live"])


def test_bars_until_stops_at_end_time_and_runs_maintenance() -> None:
    # 18:31 UTC == 14:31 ET (July); end at 14:33 ET = 873 minutes
    clock = FakeClock(datetime(2026, 7, 10, 18, 31, 30, tzinfo=timezone.utc))
    bars = [_bar("2026-07-10T18:31:00Z"), _bar("2026-07-10T18:32:00Z"),
            _bar("2026-07-10T18:33:00Z")]
    calls = []

    def maintenance():
        calls.append(clock.now())
        clock.advance(minutes=1)

    taken = list(bars_until(iter(bars), clock, 14 * 60 + 33, maintenance))

    assert len(taken) == 2  # third bar never pulled: end time hit after bar 2
    assert len(calls) == 2


def test_build_and_run_observe_session_end_to_end(tmp_path) -> None:
    """Full offline session: scripted chart events -> LiveLoop -> JSONL ->
    PARITY report. The broker's REST client is the sentinel: any order
    call would raise."""
    clock = FakeClock(datetime(2026, 7, 10, 18, 31, 30, tzinfo=timezone.utc))
    # Front contract for 2026-07-10 is NQU6 (Sep 2026) per the roll rule.
    ws = FakeChartWs([
        _eoh_event(),
        _chart_event("2026-07-10T18:31:00.000Z", 100.0),
        _chart_event("2026-07-10T18:32:00.000Z", 101.0),
        _chart_event("2026-07-10T18:33:00.000Z", 102.0),
    ])

    session = build_observe_session(
        ws_client=ws, clock=clock, account_spec="DEMO123", account_id=456,
        data_dir=tmp_path, bars_back=10, end_minutes_et=14 * 60 + 33,
        maintenance=lambda: clock.advance(minutes=1),
    )

    assert session.events_path.parent.name == "2026-07-10"
    subscribe = [r for r in ws.requests if r[0] == "md/getChart"]
    assert subscribe and subscribe[0][1]["symbol"] == "NQU6"

    reference = _reference_csv(tmp_path / "reference.csv", [
        ("2026-07-10T18:31:00Z", 100.0),
        ("2026-07-10T18:32:00Z", 101.0),
    ])
    exit_code = run_observe_session(session, reference_bars_path=reference)

    assert exit_code == 0  # clean session, PARITY
    assert ws.closed
    cancel = [r for r in ws.requests if r[0] == "md/cancelChart"]
    assert cancel == [("md/cancelChart", {"subscriptionId": 2})]
    text = session.events_path.read_text(encoding="utf-8")
    assert text.count('"bar"') == 2  # two bars before end time
    assert session.report_path.exists()
    assert "PARITY" in session.report_path.read_text(encoding="utf-8")


def test_observe_broker_rest_sentinel_raises_on_any_call(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 7, 10, 18, 31, 30, tzinfo=timezone.utc))
    session = build_observe_session(
        ws_client=FakeChartWs([]), clock=clock, account_spec="D", account_id=1,
        data_dir=tmp_path, bars_back=10, end_minutes_et=14 * 60 + 33,
    )
    rest = session.broker._rest_client
    with pytest.raises(TradovateOrderSafetyError, match="observe mode"):
        rest.order_place({"orderQty": 1})


def test_report_only_mode_runs_offline(tmp_path) -> None:
    from full_python.events import EventLedger, EventType

    ledger = EventLedger()
    ledger.append(EventType.BAR, timestamp_utc="2026-07-10T18:31:00Z",
                  payload={"symbol": "NQU6", "open": 1.0, "high": 1.0,
                           "low": 1.0, "close": 1.0, "volume": 1.0})
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)

    reference = _reference_csv(
        tmp_path / "reference.csv", [("2026-07-10T18:31:00Z", 1.0)]
    )
    assert main(["--report-only", str(events), "--reference-bars", reference]) == 0
    assert (tmp_path / "report.html").exists()


def test_ws_failure_mid_session_still_produces_report(tmp_path) -> None:
    """A TCP drop / server-close mid-session raises TradovateWebSocketError,
    which is not a LiveDataError and therefore propagates out of
    LiveLoop.run(). The runner's top-level catch-all must still deliver a
    report and a nonzero exit -- no raw traceback, no lost session."""

    class DroppingWs(FakeChartWs):
        def receive_event(self, timeout_seconds):
            raise TradovateWebSocketError("dropped")

    clock = FakeClock(datetime(2026, 7, 10, 18, 31, 30, tzinfo=timezone.utc))
    session = build_observe_session(
        ws_client=DroppingWs([]), clock=clock, account_spec="D", account_id=1,
        data_dir=tmp_path, bars_back=10, end_minutes_et=14 * 60 + 33,
    )

    exit_code = run_observe_session(session)

    assert exit_code == 2
    assert session.report_path.exists()


def test_cold_start_outage_writes_halt_ledger_and_report(tmp_path) -> None:
    """No first bar at 09:35 ET must halt, close, and leave evidence."""
    clock = FakeClock(datetime(2026, 7, 10, 13, 35, 30, tzinfo=timezone.utc))
    session = build_observe_session(
        ws_client=FakeChartWs([]),
        clock=clock,
        account_spec="D",
        account_id=1,
        data_dir=tmp_path,
        bars_back=10,
        end_minutes_et=16 * 60 + 5,
    )

    exit_code = run_observe_session(session)

    assert exit_code == 2
    assert session.ws.closed
    ledger_text = session.events_path.read_text(encoding="utf-8")
    assert '"transition": "execution_halt"' in ledger_text
    assert '"reason": "data_outage"' in ledger_text
    report_text = session.report_path.read_text(encoding="utf-8")
    assert "data_outage" in report_text
    assert "NO-DATA" in report_text


def test_report_only_names_html_from_events_stem(tmp_path) -> None:
    """--report-only on events-2.jsonl must write report-2.html, not clobber
    a first run's report.html."""
    from full_python.events import EventLedger, EventType

    ledger = EventLedger()
    ledger.append(EventType.BAR, timestamp_utc="2026-07-10T18:31:00Z",
                  payload={"symbol": "NQU6", "open": 1.0, "high": 1.0,
                           "low": 1.0, "close": 1.0, "volume": 1.0})
    events = tmp_path / "events-2.jsonl"
    ledger.write_jsonl(events)

    reference = _reference_csv(
        tmp_path / "reference.csv", [("2026-07-10T18:31:00Z", 1.0)]
    )
    assert main(["--report-only", str(events), "--reference-bars", reference]) == 0
    assert (tmp_path / "report-2.html").exists()
    assert not (tmp_path / "report.html").exists()


def test_second_same_day_run_gets_a_fresh_ledger_file(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 7, 10, 18, 31, 30, tzinfo=timezone.utc))
    first = build_observe_session(
        ws_client=FakeChartWs([]), clock=clock, account_spec="D", account_id=1,
        data_dir=tmp_path, bars_back=10, end_minutes_et=14 * 60 + 33,
    )
    second = build_observe_session(
        ws_client=FakeChartWs([]), clock=clock, account_spec="D", account_id=1,
        data_dir=tmp_path, bars_back=10, end_minutes_et=14 * 60 + 33,
    )
    assert first.events_path.name == "events.jsonl"
    assert second.events_path.name == "events-2.jsonl"
    assert second.report_path.name == "report-2.html"
