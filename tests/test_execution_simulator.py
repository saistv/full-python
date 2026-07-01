from full_python.execution.simulator import simulate_strategy_trades
from full_python.models import MarketBar, OrderIntent, SignalDecision, StrategyResult


class EntryThenStopStrategy:
    def on_bar(self, bar: MarketBar) -> StrategyResult:
        if bar.timestamp_utc == "2026-06-30T13:30:00Z":
            return StrategyResult(
                signal=SignalDecision.accepted(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="long",
                    reason="test_entry",
                    metadata={"stop_price": 95.0},
                ),
                order_intents=(
                    OrderIntent.market_entry(
                        timestamp_utc=bar.timestamp_utc,
                        symbol=bar.symbol,
                        side="buy",
                        quantity=1,
                        reason="test_entry",
                        metadata={"stop_price": 95.0},
                    ),
                ),
            )
        return StrategyResult()


class EntryEveryBarStrategy:
    def on_bar(self, bar: MarketBar) -> StrategyResult:
        return StrategyResult(
            order_intents=(
                OrderIntent.market_entry(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="buy",
                    quantity=1,
                    reason="test_entry",
                    metadata={"stop_price": 90.0},
                ),
            )
        )


def test_simulate_strategy_trades_exits_long_when_stop_touched() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 101.0, 94.75, 96.0, 12),
    ]

    ledger = simulate_strategy_trades(bars, EntryThenStopStrategy())

    assert len(ledger.trades) == 1
    trade = ledger.trades[0]
    assert trade.entry_timestamp_utc == "2026-06-30T13:30:00Z"
    assert trade.exit_timestamp_utc == "2026-06-30T13:31:00Z"
    assert trade.entry_price == 100.0
    assert trade.exit_price == 95.0
    assert trade.exit_reason == "stop"
    assert trade.pnl_points == -5.0


def test_simulate_strategy_trades_ignores_new_entries_while_position_open() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 102.0, 99.0, 101.0, 12),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 101.0, 103.0, 100.0, 102.0, 15),
    ]

    ledger = simulate_strategy_trades(bars, EntryEveryBarStrategy())

    assert len(ledger.trades) == 1
    assert ledger.trades[0].entry_timestamp_utc == "2026-06-30T13:30:00Z"
    assert ledger.trades[0].exit_timestamp_utc == "2026-06-30T13:32:00Z"
    assert ledger.trades[0].exit_reason == "end_of_data"
    assert ledger.trades[0].pnl_points == 2.0


def test_simulate_strategy_trades_force_exits_on_symbol_change() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQM2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 110.0, 112.0, 109.0, 111.0, 12),
    ]

    ledger = simulate_strategy_trades(bars, EntryEveryBarStrategy())

    assert len(ledger.trades) == 2
    assert ledger.trades[0].symbol == "NQM2026"
    assert ledger.trades[0].exit_timestamp_utc == "2026-06-30T13:31:00Z"
    assert ledger.trades[0].exit_price == 110.0
    assert ledger.trades[0].exit_reason == "symbol_change"
    assert ledger.trades[1].symbol == "NQU2026"


def test_trade_ledger_summary_counts_wins_losses_and_points() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 102.0, 99.0, 101.0, 12),
    ]

    ledger = simulate_strategy_trades(bars, EntryEveryBarStrategy())

    assert ledger.summary()["trade_count"] == 1
    assert ledger.summary()["winning_trades"] == 1
    assert ledger.summary()["losing_trades"] == 0
    assert ledger.summary()["total_pnl_points"] == 1.0
    assert ledger.summary()["exit_reason_counts"] == {"end_of_data": 1}
