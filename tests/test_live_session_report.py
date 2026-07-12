from __future__ import annotations

import os
from pathlib import Path

import pytest

from full_python.events import EventLedger, EventRecord, EventType
from full_python.live.recording import RecordingStrategy
from full_python.live.session_report import (
    bars_from_ledger,
    diff_bars,
    diff_signals,
    recorded_signals,
    replay_signals,
    run_report,
)
from full_python.models import ExitDecision, MarketBar, OrderIntent, StrategyResult


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


def _write_reference(path: Path, ledger: EventLedger) -> Path:
    bars = bars_from_ledger(ledger)
    lines = ["timestamp,symbol,open,high,low,close,volume"]
    lines.extend(
        f"{b.timestamp_utc},{b.symbol},{b.open},{b.high},{b.low},{b.close},{b.volume}"
        for b in bars
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


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

    reference = _write_reference(tmp_path / "reference.csv", ledger)
    exit_code = run_report(events, html, reference_bars_path=reference)

    assert exit_code == 0
    text = html.read_text(encoding="utf-8")
    assert "PARITY" in text
    assert "DIVERGENCE" not in text


def test_empty_ledger_is_no_data_not_vacuous_parity(tmp_path) -> None:
    ledger = EventLedger()  # zero BAR events, e.g. a startup crash
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)
    html = tmp_path / "report.html"

    reference = _write_reference(tmp_path / "reference.csv", ledger)
    exit_code = run_report(events, html, reference_bars_path=reference)

    assert exit_code == 1
    text = html.read_text(encoding="utf-8")
    assert "NO-DATA" in text
    assert "PARITY" not in text


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


def test_report_without_independent_bars_is_unverified(tmp_path) -> None:
    ledger = _ledger_with_bars(3)
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)
    html = tmp_path / "report.html"

    assert run_report(events, html) == 1
    assert "BAR-UNVERIFIED" in html.read_text(encoding="utf-8")


def test_independent_bar_difference_blocks_parity() -> None:
    captured = bars_from_ledger(_ledger_with_bars(3))
    changed = list(captured)
    bar = changed[1]
    changed[1] = MarketBar(
        timestamp_utc=bar.timestamp_utc,
        symbol=bar.symbol,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close + 1.0,
        volume=bar.volume,
    )

    differences = diff_bars(captured, changed)

    assert len(differences) == 1
    assert bar.timestamp_utc in differences[0]


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


class ScriptedStrategy:
    """Emits one real entry (bar index 2) and one real exit (bar index 4);
    also records that on_bar_context runs before every bar."""

    def __init__(self) -> None:
        self.bar_index = -1
        self.context_calls = 0

    def on_bar_context(self, *, session_pnl: float, daily_limit_hit: bool) -> None:
        self.context_calls += 1

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        self.bar_index += 1
        if self.bar_index == 2:
            return StrategyResult(order_intents=(
                OrderIntent.market_entry(
                    timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, side="buy",
                    quantity=1, reason="scripted",
                    metadata={"stop_price": bar.close - 10.0},
                ),
            ))
        if self.bar_index == 4:
            return StrategyResult(exits=(
                ExitDecision(timestamp_utc=bar.timestamp_utc, symbol=bar.symbol,
                             reason="scripted_exit"),
            ))
        return StrategyResult()


def _scripted_bars(count: int = 6) -> list:
    return [
        MarketBar(
            timestamp_utc=f"2026-07-10T18:{31 + index:02d}:00Z",
            symbol="NQU6",
            open=100.0 + index, high=100.5 + index, low=99.5 + index,
            close=100.0 + index, volume=10.0,
        )
        for index in range(count)
    ]


def test_replay_and_recorded_agree_for_a_scripted_strategy() -> None:
    bars = _scripted_bars(6)

    recorded_ledger = EventLedger()
    recorded_strategy = ScriptedStrategy()
    recording = RecordingStrategy(recorded_strategy, recorded_ledger)
    for bar in bars:
        recorded_ledger.append(EventType.BAR, timestamp_utc=bar.timestamp_utc,
                                payload=bar.to_payload())
        recording.on_bar_context(session_pnl=0.0, daily_limit_hit=False)
        recording.on_bar(bar)

    replay_instances: list = []

    def factory() -> ScriptedStrategy:
        strategy = ScriptedStrategy()
        replay_instances.append(strategy)
        return strategy

    recorded = recorded_signals(recorded_ledger)
    replay = replay_signals(bars, strategy_factory=factory)

    # Real, non-empty streams -- this is the whole point of the test.
    assert recorded and replay
    assert len(recorded) == 2  # one entry, one exit
    assert len(replay) == 2

    assert diff_signals(recorded, replay) == []

    # on_bar_context ran before every bar, on both sides.
    assert recorded_strategy.context_calls == len(bars)
    assert len(replay_instances) == 1
    assert replay_instances[0].context_calls == len(bars)

    # Mutating one recorded value produces exactly one named divergence.
    mutated = list(recorded)
    entry = mutated[0]
    mutated[0] = dict(entry, stop_price=entry["stop_price"] - 1.0)
    divergences = diff_signals(mutated, replay)
    assert len(divergences) == 1
    assert entry["minute"] in divergences[0]


@pytest.mark.skipif(
    "FULL_PYTHON_BASELINE_DATA" not in os.environ,
    reason="requires the operator's local 9-month CSV (set FULL_PYTHON_BASELINE_DATA)",
)
def test_report_on_frozen_anchor_bars_has_real_signals_and_flags_corruption(tmp_path) -> None:
    from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
    from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
    from full_python.strategy.adaptive_trend_config import production_am_config

    column_map = CsvBarColumnMap(timestamp="timestamp", symbol="symbol", open="open",
                                 high="high", low="low", close="close", volume="volume")
    bars = load_csv_bars(Path(os.environ["FULL_PYTHON_BASELINE_DATA"]), column_map)[:10000]

    ledger = EventLedger()
    strategy = RecordingStrategy(AdaptiveTrendStrategy(production_am_config()), ledger)
    for bar in bars:
        ledger.append(EventType.BAR, timestamp_utc=bar.timestamp_utc, payload=bar.to_payload())
        strategy.on_bar_context(session_pnl=0.0, daily_limit_hit=False)
        strategy.on_bar(bar)

    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)
    html = tmp_path / "report.html"

    reference = _write_reference(tmp_path / "reference.csv", ledger)
    exit_code = run_report(events, html, reference_bars_path=reference)

    assert exit_code == 0
    text = html.read_text(encoding="utf-8")
    assert "PARITY" in text

    recorded = recorded_signals(ledger)
    assert recorded, (
        "expected non-empty recorded signals within the first 10000 anchor bars; "
        "raise the bar count if this fails, don't delete the assertion"
    )

    # Corrupt one ORDER_INTENT side and confirm the report now flags a divergence.
    corrupted = EventLedger()
    flipped = False
    for record in ledger.records:
        payload = dict(record.payload)
        if not flipped and record.event_type is EventType.ORDER_INTENT:
            payload["side"] = "sell" if payload.get("side") == "buy" else "buy"
            flipped = True
        corrupted.records.append(EventRecord(
            event_id=record.event_id, event_type=record.event_type,
            timestamp_utc=record.timestamp_utc, payload=payload,
        ))
    assert flipped, "expected at least one recorded ORDER_INTENT in the anchor slice"
    corrupted.write_jsonl(events)

    exit_code = run_report(events, html, reference_bars_path=reference)
    assert exit_code == 1


def test_sim_section_degrades_gracefully_when_sim_trades_fails(tmp_path, monkeypatch) -> None:
    import full_python.live.session_report as session_report

    def _boom(bars):
        raise RuntimeError("frozen config missing")

    monkeypatch.setattr(session_report, "_sim_trades", _boom)

    ledger = _ledger_with_bars(5)
    events = tmp_path / "events.jsonl"
    ledger.write_jsonl(events)
    html = tmp_path / "report.html"

    reference = _write_reference(tmp_path / "reference.csv", ledger)
    exit_code = run_report(events, html, reference_bars_path=reference)

    assert exit_code == 0
    text = html.read_text(encoding="utf-8")
    assert "unavailable" in text
