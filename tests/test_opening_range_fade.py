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
