from __future__ import annotations

import json

from full_python.events import EventLedger, EventType
from full_python.live.session_report import (
    bars_from_ledger,
    diff_signals,
    recorded_signals,
    replay_signals,
    run_report,
)
from full_python.models import MarketBar


def _bar_payload(close: float) -> dict:
    return {"symbol": "NQU6", "open": close, "high": close + 0.5,
            "low": close - 0.5, "close": close, "volume": 10.0}


def _ledger_with_bars(count: int = 5) -> EventLedger:
    ledger = EventLedger()
    for index in range(count):
        ledger.append(
            EventType.BAR,
            timestamp_utc=f"2026-07-10T18:{31 + index:02d}:00Z",  # 14:31+ ET: quiet hours
            payload=_bar_payload(100.0 + index),
        )
    return ledger


def test_bars_roundtrip_from_ledger() -> None:
    ledger = _ledger_with_bars(3)
    bars = bars_from_ledger(ledger)
    assert [type(b) for b in bars] == [MarketBar] * 3
    assert bars[0].timestamp_utc == "2026-07-10T18:31:00Z"
    assert bars[2].close == 102.0
    assert bars[0].symbol == "NQU6"


def test_quiet_session_is_parity(tmp_path) -> None:
    ledger = _ledger_with_bars(5)
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)
    html = tmp_path / "report.html"

    exit_code = run_report(events, html)

    assert exit_code == 0
    text = html.read_text(encoding="utf-8")
    assert "PARITY" in text
    assert "DIVERGENCE" not in text


def test_bogus_recorded_signal_is_divergence(tmp_path) -> None:
    ledger = _ledger_with_bars(5)
    ledger.append(
        EventType.ORDER_INTENT,
        timestamp_utc="2026-07-10T18:33:00Z",
        payload={"symbol": "NQU6", "side": "buy", "quantity": 1,
                 "reason": "adaptive_trend", "stop_price": 95.0},
    )
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)
    html = tmp_path / "report.html"

    exit_code = run_report(events, html)

    assert exit_code == 1
    text = html.read_text(encoding="utf-8")
    assert "DIVERGENCE" in text
    assert "18:33" in text  # the divergent minute is named


def test_diff_reports_index_and_both_sides() -> None:
    live = [{"minute": "m1", "kind": "entry", "side": "buy",
             "quantity": 1, "stop_price": 95.0}]
    divergences = diff_signals(live, [])
    assert len(divergences) == 1
    assert "live=" in divergences[0] and "replay=" in divergences[0]
    assert diff_signals([], []) == []


def test_halts_are_listed_in_the_report(tmp_path) -> None:
    ledger = _ledger_with_bars(2)
    ledger.append(
        EventType.STATE_TRANSITION,
        timestamp_utc="2026-07-10T18:32:30Z",
        payload={"transition": "execution_halt", "reason": "data_outage",
                 "error": "no bar within grace"},
    )
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)
    html = tmp_path / "report.html"

    run_report(events, html)

    text = html.read_text(encoding="utf-8")
    assert "data_outage" in text
