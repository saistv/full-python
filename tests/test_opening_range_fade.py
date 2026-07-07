from full_python.strategy.opening_range_fade_config import OpeningRangeFadeConfig


def test_config_is_literature_faithful_baseline() -> None:
    c = OpeningRangeFadeConfig()
    assert c.stop_atr_mult <= 1.0            # principle 3: tight stop
    assert c.rr_multiple >= 2.0              # principle 2: R:R >= 2:1
    assert 15 <= c.time_stop_bars <= 20      # principle 4: short hold
    assert c.adx_max <= 20.0                 # principle 5: strict regime gate
    assert c.breakout_atr_mult >= 1.0        # principle 6: extension, not a poke
    assert c.entry_start_minutes_et >= 10 * 60   # disjoint from AT's 9:30-10:00
    assert c.or_start_minutes_et == 9 * 60 + 30  # OR = 9:30-10:00
    assert c.or_end_minutes_et == 10 * 60
    assert len(c.parameter_hash()) == 64


from datetime import datetime, timedelta, timezone

from full_python.events import EventType
from full_python.models import MarketBar
from full_python.simulation import SimulationConfig, SimulationEngine
from full_python.strategy.opening_range_fade import OpeningRangeFadeStrategy


# June 2026 is EDT (UTC-4), so 13:30 UTC == 9:30 ET: minute 0 = 9:30,
# minute 30 = 10:00, minute 40 = 10:10 ET. DST-clean by construction.
_BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _ts(day: int, minute: int) -> str:
    d = _BASE + timedelta(days=(day // 5) * 7 + day % 5)
    return (d.replace(hour=13, minute=30) + timedelta(minutes=minute)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _bar(ts, o, h, l, c, v=100.0):
    return MarketBar(ts, "NQ", o, h, l, c, v)


def _chop_day(day: int) -> list[MarketBar]:
    """Flat, alternating-direction session -> low daily ADX, tiny ATR."""
    bars = []
    p = 20000.0
    for m in range(390):
        c = p + (0.25 if m % 2 == 0 else -0.25)
        bars.append(_bar(_ts(day, m), p, max(p, c) + 0.25, min(p, c) - 0.25, c))
        p = c
    return bars


def _or_then_failed_up_breakout(day: int, extend_points: float) -> list[MarketBar]:
    """9:30-10:00 flat OR ~[19995, 20005]; at 10:10 a bar spikes `extend_points`
    above OR high then closes back inside -> a failed upside breakout.
    """
    bars = []
    p = 20000.0
    for m in range(390):
        if m < 30:                    # 9:30-10:00 OR: flat range around 20000
            c = 20005.0 if m % 2 == 0 else 19995.0
            h, l = 20005.0, 19995.0
        elif m == 40:                 # 10:10: spike high above OR, close inside
            c = 20003.0               # closes back inside (< or_high 20005)
            h = 20005.0 + extend_points
            l = 19999.0
        else:                         # flat elsewhere
            c = p + (0.25 if m % 2 == 0 else -0.25)
            h, l = max(p, c) + 0.25, min(p, c) - 0.25
        bars.append(_bar(_ts(day, m), p, h, l, c))
        p = c
    return bars


def _warmup(days: int = 40) -> list[MarketBar]:
    out = []
    for d in range(days):
        out.extend(_chop_day(d))
    return out


def _run(bars, config=None):
    strategy = OpeningRangeFadeStrategy(config or OpeningRangeFadeConfig())
    sim = SimulationConfig(
        point_value=20.0, commission_per_contract_round_trip=10.0,
        entry_slippage_points=0.0, exit_slippage_points=0.0,
        rth_open_extra_entry_slippage_points=0.0,
    )
    return SimulationEngine(sim).run(bars, strategy)


def _fade_fills(result):
    return [r for r in result.ledger.records
            if r.event_type == EventType.FILL
            and r.payload.get("reason") == "opening_range_fade"]


def test_failed_upside_breakout_fires_a_short_fade() -> None:
    # 40 low-ADX warmup days, then a day with a big (10pt >> 1 ATR) failed
    # upside breakout -> expect a short fade entry.
    bars = _warmup(40) + _or_then_failed_up_breakout(40, extend_points=10.0)
    result = _run(bars)
    fills = _fade_fills(result)
    assert len(fills) >= 1
    assert fills[0].payload["side"] == "sell"  # fade the failed UP breakout -> short


def test_subthreshold_poke_does_not_fade() -> None:
    # Same setup but the breakout barely pokes above the OR (0.5pt < 1 ATR):
    # not an EXTENSION, so no fade.
    bars = _warmup(40) + _or_then_failed_up_breakout(40, extend_points=0.5)
    result = _run(bars)
    assert len(_fade_fills(result)) == 0


def test_symmetric_failed_downside_breakout_fires_a_long_fade() -> None:
    def failed_down(day):
        bars = []
        p = 20000.0
        for m in range(390):
            if m < 30:
                c = 20005.0 if m % 2 == 0 else 19995.0
                h, l = 20005.0, 19995.0
            elif m == 40:                 # spike LOW below OR, close inside
                c = 19997.0               # back inside (> or_low 19995)
                h, l = 20001.0, 19995.0 - 10.0
            else:
                c = p + (0.25 if m % 2 == 0 else -0.25)
                h, l = max(p, c) + 0.25, min(p, c) - 0.25
            bars.append(_bar(_ts(day, m), p, h, l, c))
            p = c
        return bars
    bars = _warmup(40) + failed_down(40)
    fills = _fade_fills(_run(bars))
    assert len(fills) >= 1
    assert fills[0].payload["side"] == "buy"  # fade the failed DOWN breakout -> long


def _failed_up_breakout_around(day: int, start_price: float, extend_points: float) -> list[MarketBar]:
    """A failed-upside-breakout day whose OR sits around `start_price` (so it can
    continue from a trending warmup without a giant gap resetting the ATR)."""
    bars = []
    p = start_price
    or_hi, or_lo = start_price + 5.0, start_price - 5.0
    for m in range(390):
        if m < 30:
            c = or_hi if m % 2 == 0 else or_lo
            h, l = or_hi, or_lo
        elif m == 40:
            c = start_price + 3.0                       # closes back inside
            h, l = or_hi + extend_points, start_price - 1.0
        else:
            c = p + (0.25 if m % 2 == 0 else -0.25)
            h, l = max(p, c) + 0.25, min(p, c) - 0.25
        bars.append(_bar(_ts(day, m), p, h, l, c))
        p = c
    return bars


def test_trending_day_adx_gate_blocks_the_fade() -> None:
    # Strongly TRENDING warmup (steady up-drift, price carried across days) ->
    # daily ADX high -> a CLEAN failed breakout (extension clears the threshold,
    # so it reaches the ADX check) is rejected with adx_trending and NO fade.
    bars = []
    price = 20000.0
    for day in range(40):
        for m in range(390):
            c = price + 0.5  # steady intraday up-drift -> high directional ADX
            bars.append(_bar(_ts(day, m), price, c + 0.25, price - 0.25, c))
            price = c
    bars += _failed_up_breakout_around(40, round(price), extend_points=10.0)

    result = _run(bars)
    rejects = [r for r in result.ledger.records
               if r.event_type == EventType.REJECTION
               and r.payload.get("reason") == "adx_trending"]
    assert len(_fade_fills(result)) == 0   # the gate blocked the fade
    assert len(rejects) >= 1               # ...and did so via adx_trending, not vacuously


def test_persistent_breakout_expires_without_fading() -> None:
    # A breakout that EXTENDS past the OR and STAYS out (never closes back
    # inside within the failure window) is a successful extension, not a
    # failure -> the failure-window-expiry branch disarms it and no fade fires.
    def or_persistent_up(day):
        bars = []
        p = 20000.0
        for m in range(390):
            if m < 30:
                c = 20005.0 if m % 2 == 0 else 19995.0
                h, l = 20005.0, 19995.0
            elif m >= 40:
                c, h, l = 20020.0, 20025.0, 20015.0   # stays well above or_high (20005)
            else:
                c = p + (0.25 if m % 2 == 0 else -0.25)
                h, l = max(p, c) + 0.25, min(p, c) - 0.25
            bars.append(_bar(_ts(day, m), p, h, l, c))
            p = c
        return bars
    bars = _warmup(40) + or_persistent_up(40)
    assert len(_fade_fills(_run(bars))) == 0


def test_upside_breakout_must_close_back_inside_the_opening_range() -> None:
    def failed_through_bottom(day):
        bars = []
        p = 20000.0
        for m in range(390):
            if m < 30:
                c = 20005.0 if m % 2 == 0 else 19995.0
                h, l = 20005.0, 19995.0
            elif m == 40:
                c, h, l = 19990.0, 20015.0, 19989.0  # closes below OR, not inside it
            else:
                c = p + (0.25 if m % 2 == 0 else -0.25)
                h, l = max(p, c) + 0.25, min(p, c) - 0.25
            bars.append(_bar(_ts(day, m), p, h, l, c))
            p = c
        return bars

    bars = _warmup(40) + failed_through_bottom(40)

    assert len(_fade_fills(_run(bars))) == 0


def test_downside_breakout_must_close_back_inside_the_opening_range() -> None:
    def failed_through_top(day):
        bars = []
        p = 20000.0
        for m in range(390):
            if m < 30:
                c = 20005.0 if m % 2 == 0 else 19995.0
                h, l = 20005.0, 19995.0
            elif m == 40:
                c, h, l = 20010.0, 20011.0, 19985.0  # closes above OR, not inside it
            else:
                c = p + (0.25 if m % 2 == 0 else -0.25)
                h, l = max(p, c) + 0.25, min(p, c) - 0.25
            bars.append(_bar(_ts(day, m), p, h, l, c))
            p = c
        return bars

    bars = _warmup(40) + failed_through_top(40)

    assert len(_fade_fills(_run(bars))) == 0


def test_cli_build_strategy_registers_opening_range_fade() -> None:
    from full_python.cli import build_strategy
    from full_python.strategy.opening_range_fade import OpeningRangeFadeStrategy

    config, strategy = build_strategy("opening_range_fade")
    assert isinstance(strategy, OpeningRangeFadeStrategy)
    assert isinstance(config, OpeningRangeFadeConfig)
