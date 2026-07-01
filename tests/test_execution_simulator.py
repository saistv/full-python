from full_python.execution.simulator import (
    ExitConversionConfig,
    ReentryControlConfig,
    SimulationCosts,
    simulate_strategy_trades,
)
from full_python.models import MarketBar, OrderIntent, SignalDecision, StrategyResult

ZERO_COSTS = SimulationCosts(
    point_value=1.0,
    slippage_points_per_side=0.0,
    commission_per_contract=0.0,
)


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

    ledger = simulate_strategy_trades(bars, EntryThenStopStrategy(), costs=ZERO_COSTS)

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

    ledger = simulate_strategy_trades(bars, EntryEveryBarStrategy(), costs=ZERO_COSTS)

    assert len(ledger.trades) == 1
    assert ledger.trades[0].entry_timestamp_utc == "2026-06-30T13:30:00Z"
    assert ledger.trades[0].exit_timestamp_utc == "2026-06-30T13:32:00Z"
    assert ledger.trades[0].exit_reason == "end_of_data"
    assert ledger.trades[0].pnl_points == 2.0


def test_simulate_strategy_trades_force_exits_on_symbol_change() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQM2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 110.0, 112.0, 109.0, 111.0, 12),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 111.0, 113.0, 110.0, 112.0, 12),
    ]

    ledger = simulate_strategy_trades(bars, EntryEveryBarStrategy(), costs=ZERO_COSTS)

    assert len(ledger.trades) == 2
    assert ledger.trades[0].symbol == "NQM2026"
    assert ledger.trades[0].exit_timestamp_utc == "2026-06-30T13:31:00Z"
    assert ledger.trades[0].exit_price == 110.0
    assert ledger.trades[0].exit_reason == "symbol_change"
    assert ledger.trades[1].symbol == "NQU2026"
    assert ledger.trades[1].entry_timestamp_utc == "2026-06-30T13:32:00Z"


def test_simulate_strategy_trades_tracks_long_mfe_and_mae() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 108.0, 97.0, 104.0, 12),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 104.0, 106.0, 94.75, 95.0, 12),
    ]

    ledger = simulate_strategy_trades(bars, EntryThenStopStrategy(), costs=ZERO_COSTS)

    trade = ledger.trades[0]
    assert trade.max_favorable_excursion_points == 8.0
    assert trade.max_adverse_excursion_points == -5.25


def test_simulate_strategy_trades_can_exit_symbol_change_at_previous_close() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQM2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQM2026", 100.0, 103.0, 99.0, 102.0, 12),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 110.0, 112.0, 109.0, 111.0, 12),
    ]

    ledger = simulate_strategy_trades(
        bars,
        EntryEveryBarStrategy(),
        costs=ZERO_COSTS,
        symbol_change_exit_mode="previous_close",
    )

    assert ledger.trades[0].symbol == "NQM2026"
    assert ledger.trades[0].exit_timestamp_utc == "2026-06-30T13:31:00Z"
    assert ledger.trades[0].exit_price == 102.0
    assert ledger.trades[0].exit_reason == "symbol_change"
    assert ledger.summary()["assumptions"]["symbol_change_exit"] == "previous_contract_last_close"
    assert ledger.summary()["assumptions"]["symbol_change_exit_mode"] == "previous_close"


def test_simulate_strategy_trades_exits_on_mfe_trailing_stop_after_activation() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 145.0, 99.0, 140.0, 12),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 140.0, 142.0, 123.0, 125.0, 12),
    ]

    ledger = simulate_strategy_trades(
        bars,
        EntryThenStopStrategy(),
        costs=ZERO_COSTS,
        exit_conversion=ExitConversionConfig(
            mfe_trailing_activation_points=40.0,
            mfe_trailing_giveback_points=20.0,
        ),
    )

    trade = ledger.trades[0]
    assert trade.exit_timestamp_utc == "2026-06-30T13:32:00Z"
    assert trade.exit_reason == "mfe_trailing_stop"
    assert trade.exit_price == 125.0
    assert trade.trailing_stop_price == 125.0
    assert trade.exit_conversion_name == "mfe_trailing"
    assert trade.pnl_points == 25.0


def test_simulate_strategy_trades_does_not_apply_mfe_trail_on_activation_bar() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 145.0, 120.0, 140.0, 12),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 140.0, 146.0, 139.0, 142.0, 12),
    ]

    ledger = simulate_strategy_trades(
        bars,
        EntryThenStopStrategy(),
        costs=ZERO_COSTS,
        exit_conversion=ExitConversionConfig(
            mfe_trailing_activation_points=40.0,
            mfe_trailing_giveback_points=20.0,
        ),
    )

    trade = ledger.trades[0]
    assert trade.exit_reason == "end_of_data"
    assert trade.trailing_stop_price == 126.0


def test_simulate_strategy_trades_blocks_same_bar_reentry_after_exit() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 101.0, 89.0, 95.0, 12),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 95.0, 99.0, 94.0, 98.0, 12),
    ]

    ledger = simulate_strategy_trades(bars, EntryEveryBarStrategy(), costs=ZERO_COSTS)

    assert len(ledger.trades) == 2
    assert ledger.trades[0].exit_timestamp_utc == "2026-06-30T13:31:00Z"
    assert ledger.trades[0].exit_reason == "stop"
    assert ledger.trades[1].entry_timestamp_utc == "2026-06-30T13:32:00Z"


def test_simulate_strategy_trades_applies_cooldown_after_exit() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 101.0, 89.0, 95.0, 12),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 95.0, 99.0, 94.0, 98.0, 12),
        MarketBar("2026-06-30T13:33:00Z", "NQU2026", 98.0, 101.0, 97.0, 100.0, 12),
    ]

    ledger = simulate_strategy_trades(
        bars,
        EntryEveryBarStrategy(),
        costs=ZERO_COSTS,
        reentry_control=ReentryControlConfig(cooldown_bars_after_exit=1),
    )

    assert len(ledger.trades) == 2
    assert ledger.trades[0].exit_timestamp_utc == "2026-06-30T13:31:00Z"
    assert ledger.trades[1].entry_timestamp_utc == "2026-06-30T13:33:00Z"
    assert ledger.summary()["assumptions"]["reentry_control"] == "cooldown"
    assert ledger.summary()["assumptions"]["cooldown_bars_after_exit"] == 1


def test_simulate_strategy_trades_requires_fresh_breakout_after_exit() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 101.0, 89.0, 95.0, 12),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 95.0, 105.0, 94.0, 100.0, 12),
        MarketBar("2026-06-30T13:33:00Z", "NQU2026", 100.0, 106.0, 99.0, 104.0, 12),
        MarketBar("2026-06-30T13:34:00Z", "NQU2026", 104.0, 108.0, 103.0, 107.0, 12),
    ]

    ledger = simulate_strategy_trades(
        bars,
        EntryEveryBarStrategy(),
        costs=ZERO_COSTS,
        reentry_control=ReentryControlConfig(require_fresh_breakout_after_exit=True),
    )

    assert len(ledger.trades) == 2
    assert ledger.trades[0].exit_timestamp_utc == "2026-06-30T13:31:00Z"
    assert ledger.trades[1].entry_timestamp_utc == "2026-06-30T13:34:00Z"
    assert ledger.summary()["assumptions"]["reentry_control"] == "fresh_breakout"
    assert ledger.summary()["assumptions"]["require_fresh_breakout_after_exit"] is True


def test_simulate_strategy_trades_applies_fresh_breakout_clearance_after_exit() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 101.0, 89.0, 95.0, 12),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 95.0, 105.0, 94.0, 102.0, 12),
        MarketBar("2026-06-30T13:33:00Z", "NQU2026", 102.0, 106.0, 101.0, 105.25, 12),
        MarketBar("2026-06-30T13:34:00Z", "NQU2026", 105.25, 108.0, 104.0, 107.5, 12),
    ]

    ledger = simulate_strategy_trades(
        bars,
        EntryEveryBarStrategy(),
        costs=ZERO_COSTS,
        reentry_control=ReentryControlConfig(
            require_fresh_breakout_after_exit=True,
            fresh_breakout_clearance_points=1.0,
        ),
    )

    assert len(ledger.trades) == 2
    assert ledger.trades[1].entry_timestamp_utc == "2026-06-30T13:34:00Z"
    assert ledger.summary()["assumptions"]["fresh_breakout_clearance_points"] == 1.0


def test_trade_ledger_summary_counts_wins_losses_and_points() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 102.0, 99.0, 101.0, 12),
    ]

    ledger = simulate_strategy_trades(bars, EntryEveryBarStrategy(), costs=ZERO_COSTS)

    assert ledger.summary()["trade_count"] == 1
    assert ledger.summary()["winning_trades"] == 1
    assert ledger.summary()["losing_trades"] == 0
    assert ledger.summary()["total_pnl_points"] == 1.0
    assert ledger.summary()["exit_reason_counts"] == {"end_of_data": 1}


def test_simulate_strategy_trades_applies_slippage_commission_and_point_value() -> None:
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 99.0, 101.0, 98.0, 100.0, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100.0, 102.0, 99.0, 101.0, 12),
    ]

    ledger = simulate_strategy_trades(
        bars,
        EntryEveryBarStrategy(),
        costs=SimulationCosts(
            point_value=2.0,
            slippage_points_per_side=1.0,
            commission_per_contract=1.0,
        ),
    )

    trade = ledger.trades[0]
    assert trade.entry_price == 101.0
    assert trade.exit_price == 100.0
    assert trade.pnl_points == -1.0
    assert trade.gross_pnl_dollars == -2.0
    assert trade.commission_dollars == 2.0
    assert trade.net_pnl_dollars == -4.0
    assert ledger.summary()["total_net_pnl_dollars"] == -4.0
    assert ledger.summary()["assumptions"]["point_value"] == 2.0
