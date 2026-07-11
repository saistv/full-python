from __future__ import annotations

from full_python.events import EventLedger, EventType
from full_python.live.recording import RecordingStrategy
from full_python.models import ExitDecision, MarketBar, OrderIntent, StrategyResult


def _bar(ts: str = "2026-07-11T13:31:00Z") -> MarketBar:
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=100.0, high=101.0,
                     low=99.0, close=100.5, volume=10.0)


class ScriptedInner:
    def __init__(self, result: StrategyResult) -> None:
        self._result = result
        self.contexts = []

    def on_bar_context(self, *, session_pnl: float, daily_limit_hit: bool) -> None:
        self.contexts.append((session_pnl, daily_limit_hit))

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        return self._result


def test_records_intents_and_exits_and_returns_result_unchanged() -> None:
    bar = _bar()
    result = StrategyResult(
        order_intents=(OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc, symbol="NQ", side="buy", quantity=2,
            reason="adaptive_trend", metadata={"stop_price": 95.5},
        ),),
        exits=(ExitDecision(timestamp_utc=bar.timestamp_utc, symbol="NQ",
                            reason="atf_flip"),),
    )
    ledger = EventLedger()
    strategy = RecordingStrategy(ScriptedInner(result), ledger)

    returned = strategy.on_bar(bar)

    assert returned is result
    kinds = [r.event_type for r in ledger.records]
    assert kinds == [EventType.ORDER_INTENT, EventType.EXIT]
    assert ledger.records[0].payload == {
        "symbol": "NQ", "side": "buy", "quantity": 2,
        "reason": "adaptive_trend", "stop_price": 95.5,
    }
    assert ledger.records[0].timestamp_utc == bar.timestamp_utc
    assert ledger.records[1].payload["reason"] == "atf_flip"


def test_forwards_context_and_tolerates_inner_without_hook() -> None:
    inner = ScriptedInner(StrategyResult())
    strategy = RecordingStrategy(inner, EventLedger())
    strategy.on_bar_context(session_pnl=-42.0, daily_limit_hit=True)
    assert inner.contexts == [(-42.0, True)]

    class Bare:
        def on_bar(self, bar):
            return StrategyResult()

    bare = RecordingStrategy(Bare(), EventLedger())
    bare.on_bar_context(session_pnl=0.0, daily_limit_hit=False)  # no AttributeError
    assert bare.on_bar(_bar()) == StrategyResult()


def test_quiet_bar_records_nothing() -> None:
    ledger = EventLedger()
    strategy = RecordingStrategy(ScriptedInner(StrategyResult()), ledger)
    strategy.on_bar(_bar())
    assert ledger.records == []
