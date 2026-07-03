from full_python.events import EventType
from full_python.models import ExitDecision, MarketBar, OrderIntent, StrategyResult
from full_python.simulation import SimulationConfig, SimulationEngine


CONFIG = SimulationConfig(
    point_value=2.0,
    commission_per_contract_round_trip=1.0,
    entry_slippage_points=1.0,
    exit_slippage_points=0.5,
    rth_open_extra_entry_slippage_points=1.0,
)


def _bar(timestamp: str, open_: float, high: float, low: float, close: float) -> MarketBar:
    return MarketBar(
        timestamp_utc=timestamp,
        symbol="MNQU2026",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=10.0,
    )


def _buy_intent(bar: MarketBar, stop_price: float, target_price: float = None) -> StrategyResult:
    metadata = {"stop_price": stop_price, "signal_price": bar.close}
    if target_price is not None:
        metadata["target_price"] = target_price
    return StrategyResult(
        order_intents=(
            OrderIntent.market_entry(
                timestamp_utc=bar.timestamp_utc,
                symbol=bar.symbol,
                side="buy",
                quantity=1,
                reason="test_entry",
                metadata=metadata,
            ),
        )
    )


class ScriptedStrategy:
    """Replays a fixed script keyed by bar index; empty result otherwise."""

    def __init__(self, script: dict) -> None:
        self.script = script
        self.index = -1

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        self.index += 1
        entry = self.script.get(self.index)
        if entry is None:
            return StrategyResult()
        return entry(bar) if callable(entry) else entry


def _events_of(result, event_type):
    return [r for r in result.ledger.records if r.event_type == event_type]


def test_entry_fills_next_bar_open_with_slippage_and_end_of_data_close() -> None:
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
        _bar("2026-06-30T13:48:00Z", 102.0, 103.0, 101.0, 102.0),
    ]
    strategy = ScriptedStrategy({0: lambda bar: _buy_intent(bar, stop_price=70.0)})

    result = SimulationEngine(CONFIG).run(bars, strategy)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_timestamp_utc == "2026-06-30T13:47:00Z"
    assert trade.entry_price == 102.0  # 101 open + 1.0 entry slippage
    assert trade.exit_reason == "end_of_data"
    assert trade.exit_price == 101.5  # 102 close - 0.5 exit slippage
    assert trade.net_pnl == (101.5 - 102.0) * 2.0 - 1.0
    assert len(_events_of(result, EventType.FILL)) == 2
    assert len(_events_of(result, EventType.TRADE_CLOSED)) == 1


def test_rth_open_window_adds_extra_entry_slippage() -> None:
    bars = [
        _bar("2026-06-30T13:30:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:31:00Z", 101.0, 102.0, 100.0, 101.5),
    ]
    strategy = ScriptedStrategy({0: lambda bar: _buy_intent(bar, stop_price=70.0)})

    result = SimulationEngine(CONFIG).run(bars, strategy)

    assert result.trades[0].entry_price == 103.0  # 101 + 1.0 + 1.0 open-window extra


def test_intrabar_stop_fills_at_stop_with_exit_slippage() -> None:
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
        _bar("2026-06-30T13:48:00Z", 101.0, 102.0, 94.0, 96.0),
    ]
    strategy = ScriptedStrategy({0: lambda bar: _buy_intent(bar, stop_price=95.0)})

    result = SimulationEngine(CONFIG).run(bars, strategy)

    trade = result.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.exit_price == 94.5  # stop 95 - 0.5 slippage
    assert not trade.ambiguous_exit
    assert trade.mae_points >= 8.0


def test_gap_through_stop_fills_at_open_not_stop() -> None:
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
        _bar("2026-06-30T13:48:00Z", 90.0, 92.0, 89.0, 91.0),
    ]
    strategy = ScriptedStrategy({0: lambda bar: _buy_intent(bar, stop_price=95.0)})

    result = SimulationEngine(CONFIG).run(bars, strategy)

    trade = result.trades[0]
    assert trade.exit_reason == "stop_gap"
    assert trade.exit_price == 89.5  # open 90 - 0.5, NOT stop 95


def test_exit_decision_fills_next_bar_open() -> None:
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
        _bar("2026-06-30T13:48:00Z", 103.0, 104.0, 102.0, 103.5),
    ]

    def exit_signal(bar: MarketBar) -> StrategyResult:
        return StrategyResult(
            exits=(
                ExitDecision(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    reason="signal_exit",
                ),
            )
        )

    strategy = ScriptedStrategy(
        {0: lambda bar: _buy_intent(bar, stop_price=70.0), 1: exit_signal}
    )

    result = SimulationEngine(CONFIG).run(bars, strategy)

    trade = result.trades[0]
    assert trade.exit_reason == "signal_exit"
    assert trade.exit_timestamp_utc == "2026-06-30T13:48:00Z"
    assert trade.exit_price == 102.5  # open 103 - 0.5
    assert len(_events_of(result, EventType.EXIT)) == 1


def test_stop_beats_pending_exit_when_bar_gaps_through_stop() -> None:
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
        _bar("2026-06-30T13:48:00Z", 90.0, 92.0, 89.0, 91.0),
    ]

    def exit_signal(bar: MarketBar) -> StrategyResult:
        return StrategyResult(
            exits=(
                ExitDecision(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    reason="signal_exit",
                ),
            )
        )

    strategy = ScriptedStrategy(
        {0: lambda bar: _buy_intent(bar, stop_price=95.0), 1: exit_signal}
    )

    result = SimulationEngine(CONFIG).run(bars, strategy)

    assert result.trades[0].exit_reason == "stop_gap"


def test_ambiguous_bar_stop_wins_and_is_flagged() -> None:
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
        _bar("2026-06-30T13:48:00Z", 101.0, 111.0, 94.0, 105.0),
    ]
    strategy = ScriptedStrategy(
        {0: lambda bar: _buy_intent(bar, stop_price=95.0, target_price=110.0)}
    )

    result = SimulationEngine(CONFIG).run(bars, strategy)

    trade = result.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.ambiguous_exit
    assert trade.exit_price == 94.5


def test_clean_target_hit_exits_at_target() -> None:
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
        _bar("2026-06-30T13:48:00Z", 105.0, 111.0, 104.0, 108.0),
    ]
    strategy = ScriptedStrategy(
        {0: lambda bar: _buy_intent(bar, stop_price=95.0, target_price=110.0)}
    )

    result = SimulationEngine(CONFIG).run(bars, strategy)

    trade = result.trades[0]
    assert trade.exit_reason == "target"
    assert not trade.ambiguous_exit
    assert trade.exit_price == 109.5  # target 110 - 0.5


def test_premarket_intent_is_vetoed_outside_rth() -> None:
    bars = [
        _bar("2026-06-30T13:28:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:29:00Z", 101.0, 102.0, 100.0, 101.5),
    ]
    strategy = ScriptedStrategy({0: lambda bar: _buy_intent(bar, stop_price=70.0)})

    result = SimulationEngine(CONFIG).run(bars, strategy)

    assert result.trades == ()
    vetoes = _events_of(result, EventType.RISK_VETO)
    assert len(vetoes) == 1
    assert vetoes[0].payload["veto_reason"] == "outside_rth"
    assert _events_of(result, EventType.ORDER_INTENT) == []


def test_intent_without_stop_is_vetoed_fail_closed() -> None:
    def no_stop_intent(bar: MarketBar) -> StrategyResult:
        return StrategyResult(
            order_intents=(
                OrderIntent.market_entry(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="buy",
                    quantity=1,
                    reason="unprotected",
                ),
            )
        )

    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
    ]

    result = SimulationEngine(CONFIG).run(bars, ScriptedStrategy({0: no_stop_intent}))

    assert result.trades == ()
    assert _events_of(result, EventType.RISK_VETO)[0].payload["veto_reason"] == "missing_stop"


def test_backstop_flattens_at_1559_eastern() -> None:
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
        _bar("2026-06-30T19:59:00Z", 105.0, 106.0, 104.0, 105.5),
    ]
    strategy = ScriptedStrategy({0: lambda bar: _buy_intent(bar, stop_price=70.0)})

    result = SimulationEngine(CONFIG).run(bars, strategy)

    trade = result.trades[0]
    assert trade.exit_reason == "session_flatten"
    assert trade.exit_timestamp_utc == "2026-06-30T19:59:00Z"
    assert trade.exit_price == 105.0  # close 105.5 - 0.5


def test_position_held_across_session_boundary_closes_at_prior_bar() -> None:
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
        _bar("2026-07-01T13:46:00Z", 108.0, 109.0, 107.0, 108.5),
    ]
    strategy = ScriptedStrategy({0: lambda bar: _buy_intent(bar, stop_price=70.0)})

    result = SimulationEngine(CONFIG).run(bars, strategy)

    trade = result.trades[0]
    assert trade.exit_reason == "session_end"
    assert trade.exit_timestamp_utc == "2026-06-30T13:47:00Z"
    assert trade.exit_price == 101.0  # prior close 101.5 - 0.5


def test_signal_bar_close_mode_fills_on_signal_bar() -> None:
    config = SimulationConfig(
        point_value=2.0,
        commission_per_contract_round_trip=1.0,
        entry_slippage_points=1.0,
        exit_slippage_points=0.5,
        rth_open_extra_entry_slippage_points=1.0,
        fill_timing="signal_bar_close",
    )
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
    ]
    strategy = ScriptedStrategy({0: lambda bar: _buy_intent(bar, stop_price=70.0)})

    result = SimulationEngine(config).run(bars, strategy)

    trade = result.trades[0]
    assert trade.entry_timestamp_utc == "2026-06-30T13:46:00Z"
    assert trade.entry_price == 101.5  # close 100.5 + 1.0


def test_two_runs_produce_identical_event_logs() -> None:
    bars = [
        _bar("2026-06-30T13:46:00Z", 100.0, 101.0, 99.0, 100.5),
        _bar("2026-06-30T13:47:00Z", 101.0, 102.0, 100.0, 101.5),
        _bar("2026-06-30T13:48:00Z", 101.0, 102.0, 94.0, 96.0),
    ]

    def make_strategy():
        return ScriptedStrategy({0: lambda bar: _buy_intent(bar, stop_price=95.0)})

    first = SimulationEngine(CONFIG).run(bars, make_strategy())
    second = SimulationEngine(CONFIG).run(bars, make_strategy())

    assert [r.to_dict() for r in first.ledger.records] == [
        r.to_dict() for r in second.ledger.records
    ]
