from scripts.sweep_ma_lengths import BASELINE_CELL, GRID_MA_50, GRID_MA_200, build_grid


def test_grid_is_preregistered_5x5():
    # These literals are locked by the design spec
    # (docs/superpowers/specs/2026-07-05-sweep-harness-design.md).
    # Changing them is changing the registered experiment -- this test
    # exists to make that impossible to do silently.
    assert GRID_MA_50 == (30, 40, 50, 60, 70)
    assert GRID_MA_200 == (100, 150, 200, 250, 300)
    assert BASELINE_CELL == (50, 200)

    grid = build_grid()
    assert len(grid) == 25
    assert grid.count({}) == 1  # baseline is the empty-override cell
    pairs = {
        (c.get("ma_50_length", 50), c.get("ma_200_length", 200)) for c in grid
    }
    assert len(pairs) == 25
    assert BASELINE_CELL in pairs
