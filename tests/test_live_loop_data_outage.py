from datetime import date, datetime, timezone

from full_python.events import EventLedger, EventType
from full_python.execution.live_loop import LiveLoop
from full_python.execution.paper_broker import PaperBroker
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.livedata.contract_authority import ContractAuthority
from full_python.livedata.live_bar_source import ActiveWindow, LiveBarSource
from full_python.livedata.feed import VendorBar
from full_python.models import OrderIntent, StrategyResult
from full_python.simulation import SimulationConfig


class FakeClock:
    def __init__(self, now): self._now = now
    def now(self): return self._now


class ScriptedFeed:
    def __init__(self, items): self._items = list(items); self._i = 0
    def next_bar(self, timeout_seconds):
        if self._i >= len(self._items): return None
        item = self._items[self._i]; self._i += 1; return item


AUTH = ContractAuthority(root="NQ")
FRONT = AUTH.front_contract(date(2025, 11, 3))
CFG = SimulationConfig(point_value=2.0, commission_per_contract_round_trip=1.0,
                       entry_slippage_points=0.0, exit_slippage_points=0.0,
                       rth_open_extra_entry_slippage_points=0.0)


def _vbar(ts, c, o=None):
    o = c if o is None else o
    return VendorBar(symbol=FRONT, timestamp_utc=ts, open=o, high=c, low=c, close=c, volume=5.0)


class EnterThenSilent:
    """Buys on the first bar (fills next bar), then never signals."""
    def __init__(self): self._fired = False
    def on_bar(self, bar):
        if not self._fired:
            self._fired = True
            return StrategyResult(order_intents=(OrderIntent.market_entry(
                timestamp_utc=bar.timestamp_utc, symbol=bar.symbol, side="buy",
                quantity=1, reason="scripted",
                metadata={"stop_price": bar.close - 50.0, "signal_price": bar.close}),))
        return StrategyResult()


def test_mid_position_outage_flattens_and_halts():
    # bars during RTH (armed window); a position opens, then the feed dries up.
    feed = ScriptedFeed([
        _vbar("2025-11-03T14:31:00Z", 100.0),  # signal -> entry pending
        _vbar("2025-11-03T14:32:00Z", 101.0),  # entry fills at open; position open
        None,                                   # feed stalls while in position -> outage
    ])
    ledger = EventLedger()
    strategy = EnterThenSilent()
    broker = PaperBroker(CFG, strategy, ledger)
    window = ActiveWindow(start_minutes_et=9 * 60 + 30, end_minutes_et=16 * 60)
    src = LiveBarSource(feed, FakeClock(datetime(2025, 11, 3, 15, 0, tzinfo=timezone.utc)),
                        AUTH, window, position_provider=lambda: broker.position is not None)
    sup = RiskSupervisor(RiskSupervisorConfig(point_value=CFG.point_value))

    result = LiveLoop(src, strategy, broker, sup, ledger).run()

    assert result.halted_reason is not None
    assert "data_outage" in result.halted_reason
    # the open position was flattened (a trade exists and the flatten closed it)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "data_outage"
    # a data_outage halt transition is in the ledger
    halts = [r for r in ledger.records
             if r.event_type == EventType.STATE_TRANSITION
             and r.payload.get("transition") == "execution_halt"
             and r.payload.get("reason") == "data_outage"]
    assert len(halts) == 1
