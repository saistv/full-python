from full_python.events import EventType
from full_python.models import MarketBar, OrderIntent, SignalDecision, StrategyResult
from full_python.replay import ReplayEngine


class BreakoutOnceStrategy:
    def on_bar(self, bar: MarketBar) -> StrategyResult:
        if bar.close > 100:
            return StrategyResult(
                signal=SignalDecision.accepted(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="long",
                    reason="breakout",
                ),
                order_intents=(
                    OrderIntent.market_entry(
                        timestamp_utc=bar.timestamp_utc,
                        symbol=bar.symbol,
                        side="buy",
                        quantity=1,
                        reason="breakout",
                    ),
                ),
            )
        return StrategyResult(
            signal=SignalDecision.rejected(
                timestamp_utc=bar.timestamp_utc,
                symbol=bar.symbol,
                side="long",
                reason="below_breakout_level",
            )
        )


def test_replay_logs_bars_signals_rejections_and_order_intents_in_order() -> None:
    bars = [
        MarketBar(
            timestamp_utc="2026-06-30T13:30:00Z",
            symbol="NQU2026",
            open=99.0,
            high=100.0,
            low=98.5,
            close=99.5,
            volume=100,
        ),
        MarketBar(
            timestamp_utc="2026-06-30T13:31:00Z",
            symbol="NQU2026",
            open=99.5,
            high=101.25,
            low=99.25,
            close=101.0,
            volume=125,
        ),
    ]

    ledger = ReplayEngine().run(bars, BreakoutOnceStrategy())

    assert [record.event_type for record in ledger.records] == [
        EventType.BAR,
        EventType.SIGNAL_DECISION,
        EventType.REJECTION,
        EventType.BAR,
        EventType.SIGNAL_DECISION,
        EventType.ORDER_INTENT,
    ]
    assert [record.event_id for record in ledger.records] == [
        "evt-00000001",
        "evt-00000002",
        "evt-00000003",
        "evt-00000004",
        "evt-00000005",
        "evt-00000006",
    ]
    assert ledger.records[2].payload["reason"] == "below_breakout_level"
    assert ledger.records[5].payload["side"] == "buy"
