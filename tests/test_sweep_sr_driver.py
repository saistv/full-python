from scripts.sweep_sr_interaction import (
    BASELINE_CELL,
    GRID_SR_BUF,
    GRID_SR_MIN,
    build_grid,
)


def test_sr_grid_is_preregistered_5x4():
    # These literals are locked by the design spec
    # (docs/superpowers/specs/2026-07-05-sr-interaction-sweep-design.md).
    # Changing them is changing the registered experiment -- this test
    # exists to make that impossible to do silently.
    assert GRID_SR_MIN == (10.0, 12.0, 15.0, 18.0, 20.0)
    assert GRID_SR_BUF == (3.0, 5.0, 7.0, 9.0)
    assert BASELINE_CELL == (15.0, 5.0)

    grid = build_grid()
    assert len(grid) == 20
    assert grid.count({}) == 1  # baseline is the empty-override cell
    pairs = {
        (c.get("sr_min_stop_distance", 15.0), c.get("sr_stop_buffer", 5.0))
        for c in grid
    }
    assert len(pairs) == 20
    assert BASELINE_CELL in pairs
    # overrides must be floats (config fields are float; int overrides
    # would alter parameter_hash semantics without changing behavior)
    for cell in grid:
        for value in cell.values():
            assert isinstance(value, float)
