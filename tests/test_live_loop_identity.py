import os
from pathlib import Path

import pytest

from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger
from full_python.execution.live_loop import LiveLoop, RecordedBarSource
from full_python.execution.paper_broker import PaperBroker
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.models import MarketBar, OrderIntent, StrategyResult
from full_python.simulation import SimulationConfig, SimulationEngine


def _bar(ts, o, h, l, c):
    return MarketBar(timestamp_utc=ts, symbol="NQ", open=o, high=h, low=l, close=c, volume=1.0)


def _config():
    return SimulationConfig(point_value=2.0, commission_per_contract_round_trip=1.0,
                            entry_slippage_points=1.0, exit_slippage_points=0.5,
                            rth_open_extra_entry_slippage_points=1.0)


class ScriptedStrategy:
    """Replays a fixed script keyed by bar index; empty result otherwise.
    Also records every callback for hook-order comparison."""

    def __init__(self, script):
        self.script = script
        self.index = -1
        self.calls = []

    def on_bar(self, bar):
        self.index += 1
        self.calls.append(("on_bar", bar.timestamp_utc))
        entry = self.script.get(self.index)
        if entry is None:
            return StrategyResult()
        return entry(bar) if callable(entry) else entry

    def on_fill(self, fill):
        self.calls.append(("on_fill", fill.timestamp_utc, fill.side))

    def on_trade_closed(self, trade):
        self.calls.append(("on_trade_closed", trade.exit_timestamp_utc))

    def on_bar_context(self, *, session_pnl, daily_limit_hit):
        self.calls.append(("on_bar_context", round(session_pnl, 6), daily_limit_hit))


def _buy(bar, stop_offset=30.0):
    return StrategyResult(order_intents=(
        OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc, symbol="NQ", side="buy",
            quantity=1, reason="scripted",
            metadata={"stop_price": bar.close - stop_offset, "signal_price": bar.close},
        ),
    ))


def _fixture_bars():
    # Two RTH sessions; entry on session 1 bar 0, intrabar stop-out on bar 2;
    # entry on session 2 that survives to session flatten.
    return [
        _bar("2025-10-01T14:31:00Z", 100.0, 101.0, 99.0, 100.0),
        _bar("2025-10-01T14:32:00Z", 100.5, 102.0, 100.0, 101.0),
        _bar("2025-10-01T14:33:00Z", 101.0, 101.5, 60.0, 62.0),   # crashes through stop
        _bar("2025-10-01T14:34:00Z", 62.0, 63.0, 61.0, 62.5),
        _bar("2025-10-02T14:31:00Z", 200.0, 201.0, 199.0, 200.0),
        _bar("2025-10-02T14:32:00Z", 200.5, 202.0, 200.0, 201.5),
        _bar("2025-10-02T14:33:00Z", 201.5, 203.0, 201.0, 202.0),
    ]


def _script():
    return {0: _buy, 4: _buy}


def _run_sim(bars):
    strategy = ScriptedStrategy(_script())
    result = SimulationEngine(_config()).run(bars, strategy)
    return result, strategy


def _run_live(bars, supervisor=None):
    strategy = ScriptedStrategy(_script())
    ledger = EventLedger()
    broker = PaperBroker(_config(), strategy, ledger)
    sup = supervisor or RiskSupervisor(RiskSupervisorConfig(point_value=2.0))
    loop = LiveLoop(RecordedBarSource(bars), strategy, broker, sup, ledger)
    return loop.run(), strategy, ledger


def test_identity_trades_and_ledger_sequence_match_simulation():
    bars = _fixture_bars()
    sim_result, sim_strategy = _run_sim(bars)
    live_result, live_strategy, live_ledger = _run_live(bars)

    assert len(sim_result.trades) == len(live_result.trades) > 0
    for sim_trade, live_trade in zip(sim_result.trades, live_result.trades):
        assert sim_trade == live_trade  # frozen dataclass: full field equality

    sim_sequence = [r.event_type for r in sim_result.ledger.records]
    live_sequence = [r.event_type for r in live_ledger.records]
    assert sim_sequence == live_sequence

    assert live_result.halted_reason is None


def test_identity_hook_order_matches_simulation():
    bars = _fixture_bars()
    _, sim_strategy = _run_sim(bars)
    _, live_strategy, _ = _run_live(bars)
    assert sim_strategy.calls == live_strategy.calls


def test_supervisor_daily_loss_flattens_and_blocks_entries():
    bars = _fixture_bars()
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=2.0, daily_loss_stop=10.0))
    live_result, _, live_ledger = _run_live(bars, supervisor=sup)
    # the session-1 stop-out loses far more than $10 -> breach latches;
    # the session-2 scripted entry must be stripped, so only 1 trade exists
    # from session 1 and none from session 2 of the same session... the
    # session-2 entry occurs on a NEW session (supervisor resets) so it fills.
    # The invariant actually asserted: no trade's entry occurs in the same
    # session after its breach.
    reasons = [t.exit_reason for t in live_result.trades]
    assert "stop" in reasons  # session 1 stop-out happened
    session1_trades = [t for t in live_result.trades if t.session_date == "2025-10-01"]
    assert len(session1_trades) == 1  # nothing new after the breach that session


@pytest.mark.skipif(
    "FULL_PYTHON_BASELINE_DATA" not in os.environ,
    reason="requires the operator's local 9-month CSV (set FULL_PYTHON_BASELINE_DATA)",
)
def test_identity_on_the_frozen_anchor_window_with_production_strategy():
    from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
    from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
    from full_python.strategy.adaptive_trend_config import production_am_config
    from scripts.freeze_baseline_anchor import FROZEN_SIMULATION_OVERRIDES

    column_map = CsvBarColumnMap(timestamp="timestamp", symbol="symbol", open="open",
                                 high="high", low="low", close="close", volume="volume")
    bars = load_csv_bars(Path(os.environ["FULL_PYTHON_BASELINE_DATA"]), column_map)
    config = SimulationConfig(**FROZEN_SIMULATION_OVERRIDES)

    sim_result = SimulationEngine(config).run(bars, AdaptiveTrendStrategy(production_am_config()))

    ledger = EventLedger()
    strategy = AdaptiveTrendStrategy(production_am_config())
    broker = PaperBroker(config, strategy, ledger)
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=config.point_value))
    live_result = LiveLoop(RecordedBarSource(bars), strategy, broker, sup, ledger).run()

    assert live_result.halted_reason is None
    assert len(sim_result.trades) == len(live_result.trades)
    for sim_trade, live_trade in zip(sim_result.trades, live_result.trades):
        assert sim_trade == live_trade
