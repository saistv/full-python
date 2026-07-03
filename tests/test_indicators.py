import pytest

from full_python.indicators import (
    AdaptiveTrendFlow,
    Atr,
    Ema,
    LinregEndpoint,
    PivotHigh,
    PivotLow,
    PopulationStdev,
    Rma,
    Sma,
    SqueezeMomentum,
    TrueRange,
)


def test_ema_seeds_with_first_value_pine_style() -> None:
    ema = Ema(3)  # alpha = 0.5

    assert ema.update(10.0) == 10.0
    assert ema.update(13.0) == 11.5


def test_sma_returns_none_until_window_full() -> None:
    sma = Sma(3)

    assert sma.update(1.0) is None
    assert sma.update(2.0) is None
    assert sma.update(3.0) == 2.0
    assert sma.update(6.0) == pytest.approx(11.0 / 3.0)


def test_population_stdev_uses_ddof_zero() -> None:
    stdev = PopulationStdev(3)
    stdev.update(1.0)
    stdev.update(2.0)

    assert stdev.update(3.0) == pytest.approx((2.0 / 3.0) ** 0.5)


def test_rma_seeds_with_sma_then_wilder_smooths() -> None:
    rma = Rma(3)

    assert rma.update(3.0) is None
    assert rma.update(6.0) is None
    assert rma.update(9.0) == 6.0
    assert rma.update(12.0) == pytest.approx(8.0)


def test_true_range_first_bar_is_high_low_then_uses_prior_close() -> None:
    tr = TrueRange()

    assert tr.update(105.0, 100.0, 102.0) == 5.0
    # Gap: high 115, low 111 vs prior close 102 -> TR = 115 - 102
    assert tr.update(115.0, 111.0, 114.0) == 13.0


def test_atr_is_rma_of_true_range() -> None:
    atr = Atr(2)

    assert atr.update(105.0, 100.0, 102.0) is None
    value = atr.update(106.0, 101.0, 104.0)  # TR2 = max(5, 4, 1) = 5
    assert value == 5.0  # SMA seed of [5, 5]


def test_linreg_endpoint_matches_brute_force_least_squares() -> None:
    linreg = LinregEndpoint(4)
    values = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0]
    results = [linreg.update(value) for value in values]

    assert results[:3] == [None, None, None]
    for end in range(3, len(values)):
        window = values[end - 3 : end + 1]
        xs = list(range(4))
        mean_x = sum(xs) / 4.0
        mean_y = sum(window) / 4.0
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, window)) / sum(
            (x - mean_x) ** 2 for x in xs
        )
        expected = mean_y + slope * (3 - mean_x)
        assert results[end] == pytest.approx(expected)


def test_linreg_endpoint_on_perfect_line_returns_last_value() -> None:
    linreg = LinregEndpoint(4)
    for value in [1.0, 2.0, 3.0]:
        linreg.update(value)

    assert linreg.update(4.0) == pytest.approx(4.0)


def test_pivot_high_confirms_late_and_shifts_like_pine() -> None:
    pivot = PivotHigh(2, 2)
    highs = [1.0, 2.0, 5.0, 3.0, 2.0, 4.0, 4.5]
    results = [pivot.update(high) for high in highs]

    # Raw pivot (5.0 at index 2) confirms at index 4; the [1]-shifted,
    # fixnan-held view first shows it at index 5 and holds it after.
    assert results[:5] == [None, None, None, None, None]
    assert results[5] == 5.0
    assert results[6] == 5.0


def test_pivot_tie_confirms_later_bar_like_pine() -> None:
    # Equal highs at indexes 1 and 2: Pine treats the LATER tied bar as the
    # pivot (non-strict left, strict right) — verified against TV on real
    # NQ ties (2026-01-19, 2026-05-13). The value confirms from the later
    # bar's window and appears after shift+fixnan at index 5.
    pivot = PivotHigh(2, 2)
    results = [pivot.update(high) for high in [1.0, 5.0, 5.0, 3.0, 2.0, 2.0]]
    assert results[:5] == [None, None, None, None, None]
    assert results[5] == 5.0

    # A tie to the RIGHT of the candidate blocks it (strict right).
    pivot = PivotHigh(2, 2)
    results = [pivot.update(high) for high in [1.0, 2.0, 5.0, 5.0, 2.0, 1.0, 1.0]]
    assert results[6] == 5.0  # only the later 5 (index 3) is the pivot


def test_pivot_low_mirrors_pivot_high() -> None:
    pivot = PivotLow(2, 2)
    lows = [9.0, 8.0, 3.0, 6.0, 7.0, 8.0]
    results = [pivot.update(low) for low in lows]

    assert results[5] == 3.0


def test_atf_warms_up_then_flips_on_band_break() -> None:
    atf = AdaptiveTrendFlow(length=3, smooth=2, sensitivity=1.0)

    states = [atf.update(10.0, 10.0, 10.0) for _ in range(3)]
    assert states[0].trend == 0
    assert states[1].trend == 0
    assert states[2].trend == -1  # close == basis -> not above -> short

    jumped = atf.update(20.0, 20.0, 20.0)
    assert jumped.trend == 1  # close broke the upper band


def test_squeeze_release_and_green_momentum_on_trending_tape() -> None:
    squeeze = SqueezeMomentum(bb_length=20, bb_mult=2.0, kc_length=20, kc_mult=1.5)

    state = None
    # Momentum needs 20 valid midline deltas, and the midline itself needs
    # 20 bars — matching Pine, the value warms up near bar 39. The tape must
    # ACCELERATE: momentum_green requires val > val[1], and a constant-slope
    # ramp produces constant momentum.
    for i in range(1, 61):
        close = float(i) ** 2 / 10.0
        state = squeeze.update(close + 0.5, close - 0.5, close)

    assert state.released
    assert state.momentum_green
    assert not state.momentum_red
    assert state.value > 0
