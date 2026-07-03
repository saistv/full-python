from datetime import datetime, timedelta, timezone

from full_python.events import EventType
from full_python.models import MarketBar, OrderIntent, StrategyResult, Trade
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import AdaptiveTrendConfig, production_am_config


def _trade(net_pnl: float, exit_reason: str = "atf_flip") -> Trade:
    return Trade(
        symbol="NQ", side="long", quantity=1,
        entry_timestamp_utc="2026-06-30T13:33:00Z", entry_price=100.0,
        exit_timestamp_utc="2026-06-30T13:40:00Z", exit_price=110.0,
        exit_reason=exit_reason, stop_price=80.0,
        gross_points=10.0, gross_pnl=net_pnl + 10.0, commission=10.0,
        net_pnl=net_pnl, mfe_points=1.0, mae_points=1.0, session_date="2026-06-30",
    )


def test_am_streak_ramps_on_wins_and_resets_on_any_non_win() -> None:
    strategy = AdaptiveTrendStrategy(production_am_config())

    for expected in (1, 2, 3):
        strategy.on_trade_closed(_trade(500.0))
        assert strategy._win_streak == expected

    strategy.on_trade_closed(_trade(0.0))  # scratch = non-win in "Any Non-Win"
    assert strategy._win_streak == 0

    strategy.on_trade_closed(_trade(500.0))
    strategy.on_trade_closed(_trade(-650.0, exit_reason="stop"))
    assert strategy._win_streak == 0


def test_am_streak_ignored_when_disabled() -> None:
    strategy = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    strategy.on_trade_closed(_trade(500.0))
    assert strategy._win_streak == 0


def test_dll_safe_quantity_matches_pine_guard() -> None:
    strategy = AdaptiveTrendStrategy(production_am_config())

    # Fresh day: budget $1,000; 20pt stop @ $20/pt = $400/contract -> 2 fit.
    strategy.on_bar_context(session_pnl=0.0, daily_limit_hit=False)
    assert strategy._dll_safe_quantity(100.0, 80.0, 4) == 2

    # After a $650 loss: budget $350 < one contract's $400 -> blocked.
    strategy.on_bar_context(session_pnl=-650.0, daily_limit_hit=False)
    assert strategy._dll_safe_quantity(100.0, 80.0, 4) == 0

    # Exact-fit boundary: budget $400 vs $400 risk -> the epsilon makes it 0,
    # matching Pine's floor((budget - 0.000001) / risk).
    strategy.on_bar_context(session_pnl=-600.0, daily_limit_hit=False)
    assert strategy._dll_safe_quantity(100.0, 80.0, 4) == 0

    # Guard off -> plan passes through untouched.
    flat = AdaptiveTrendStrategy(AdaptiveTrendConfig())
    flat.on_bar_context(session_pnl=-650.0, daily_limit_hit=False)
    assert flat._dll_safe_quantity(100.0, 80.0, 3) == 3


class _OneShotLongStrategy:
    """Enters 2 contracts on the first bar with a far stop, then holds."""

    def __init__(self) -> None:
        self.fired = False
        self.context_log: list[tuple[float, bool]] = []

    def on_bar_context(self, *, session_pnl: float, daily_limit_hit: bool) -> None:
        self.context_log.append((session_pnl, daily_limit_hit))

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        if self.fired:
            return StrategyResult()
        self.fired = True
        intent = OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, side="buy",
            quantity=2, reason="sr_breakout",
            metadata={"stop_price": bar.close - 500.0, "signal_price": bar.close},
        )
        return StrategyResult(order_intents=(intent,))


def _bars(closes: list[float], start_minute: int = 0) -> list[MarketBar]:
    base = datetime(2026, 6, 30, 14, 0, tzinfo=timezone.utc)  # 10:00 ET
    bars = []
    previous = closes[0]
    for i, close in enumerate(closes):
        timestamp = (base + timedelta(minutes=start_minute + i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bars.append(MarketBar(
            timestamp_utc=timestamp, symbol="NQ", open=previous,
            high=max(previous, close) + 0.5, low=min(previous, close) - 0.5,
            close=close, volume=10.0,
        ))
        previous = close
    return bars


def test_engine_dll_flattens_next_open_and_blocks_entries() -> None:
    # Long 2 contracts from bar 2 open (20000). $20/pt: a 30pt drop is
    # -$1,200 unrealized -> DLL breach at that bar's close; flatten fills
    # at the NEXT bar's open; the stop (500pt away) never fires.
    closes = [20000.0, 20000.0, 19990.0, 19970.0, 19965.0, 19960.0]
    config = SimulationConfig(
        point_value=20.0, commission_per_contract_round_trip=10.0,
        entry_slippage_points=0.0, exit_slippage_points=0.0,
        rth_open_extra_entry_slippage_points=0.0, daily_loss_limit=1000.0,
    )
    strategy = _OneShotLongStrategy()
    result = SimulationEngine(config).run(_bars(closes), strategy)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "daily_limit"
    # Breach detected on the 19970 close (-$1,220); fill at next open.
    assert trade.exit_timestamp_utc.endswith("14:04:00Z")
    assert trade.exit_price == 19970.0  # next bar's open (= prior close here)

    transitions = [
        r for r in result.ledger.records
        if r.event_type == EventType.STATE_TRANSITION
        and r.payload.get("transition") == "daily_limit_hit"
    ]
    assert len(transitions) == 1

    # Context hook saw the halt from the breach bar onward.
    assert strategy.context_log[3][1] is True
    assert strategy.context_log[-1][1] is True


def test_engine_dll_veto_and_next_session_reset() -> None:
    config = SimulationConfig(
        point_value=20.0, commission_per_contract_round_trip=0.0,
        entry_slippage_points=0.0, exit_slippage_points=0.0,
        rth_open_extra_entry_slippage_points=0.0, daily_loss_limit=1000.0,
    )

    class _RetryStrategy(_OneShotLongStrategy):
        """Fires once, and tries again right after the DLL halt."""

        def __init__(self) -> None:
            super().__init__()
            self.currently_halted = False

        def on_bar_context(self, *, session_pnl: float, daily_limit_hit: bool) -> None:
            super().on_bar_context(session_pnl=session_pnl, daily_limit_hit=daily_limit_hit)
            self.currently_halted = daily_limit_hit

        def on_bar(self, bar: MarketBar) -> StrategyResult:
            result = super().on_bar(bar)
            if self.currently_halted and not result.order_intents:
                intent = OrderIntent.market_entry(
                    timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, side="buy",
                    quantity=1, reason="sr_breakout",
                    metadata={"stop_price": bar.close - 500.0, "signal_price": bar.close},
                )
                return StrategyResult(order_intents=(intent,))
            return result

    day1 = _bars([20000.0, 20000.0, 19990.0, 19970.0, 19965.0, 19960.0, 19958.0])
    # Next CME session (after 18:00 ET boundary).
    day2_base = datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc)
    day2 = []
    for i in range(3):
        ts = (day2_base + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        day2.append(MarketBar(ts, "NQ", 20000.0, 20000.5, 19999.5, 20000.0, 10.0))

    strategy = _RetryStrategy()
    result = SimulationEngine(config).run(day1 + day2, strategy)

    vetoes = [
        r for r in result.ledger.records
        if r.event_type == EventType.RISK_VETO
        and r.payload.get("veto_reason") == "daily_limit"
    ]
    assert vetoes, "post-halt entry attempt must be vetoed with reason daily_limit"

    # Next session: halt lifted, baseline re-anchored (context session_pnl == 0).
    day2_contexts = strategy.context_log[len(day1):]
    assert day2_contexts[0] == (0.0, False)
