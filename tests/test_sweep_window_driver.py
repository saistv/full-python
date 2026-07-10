from scripts.sweep_entry_window import (
    BASELINE_CELL,
    GRID_DURATIONS,
    GRID_STARTS,
    build_grid,
)


def test_window_grid_is_preregistered_4x2():
    # Locked by the entry-window sweep pre-registration; changing these
    # literals changes the registered experiment -- this test makes that
    # impossible to do silently.
    assert GRID_STARTS == (570, 585, 600, 630)      # 9:30 / 9:45 / 10:00 / 10:30
    assert GRID_DURATIONS == (30, 60)
    assert BASELINE_CELL == (570, 600)              # 9:30-10:00, production window

    grid = build_grid()
    assert len(grid) == 8
    assert grid.count({}) == 1  # baseline is the empty-override cell
    pairs = {
        (c.get("entry_start_minutes_et", 570), c.get("entry_end_minutes_et", 600))
        for c in grid
    }
    assert len(pairs) == 8
    assert BASELINE_CELL in pairs
    # every non-baseline cell has end = start + a registered duration, and
    # all override values are ints (config fields are int)
    for cell in grid:
        for value in cell.values():
            assert isinstance(value, int)
        if cell:
            assert cell["entry_end_minutes_et"] - cell["entry_start_minutes_et"] in GRID_DURATIONS
