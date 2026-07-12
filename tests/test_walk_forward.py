import pytest

from full_python.models import Trade
from full_python.research.walk_forward import build_anchored_folds, summarize_walk_forward


def _trade(day: str, pnl: float) -> Trade:
    return Trade(
        symbol="NQ", side="long", quantity=1,
        entry_timestamp_utc=f"{day}T14:30:00Z", entry_price=100.0,
        exit_timestamp_utc=f"{day}T15:00:00Z", exit_price=100.0,
        exit_reason="test", stop_price=95.0, gross_points=0.0,
        gross_pnl=pnl, commission=0.0, net_pnl=pnl,
        mfe_points=0.0, mae_points=0.0, session_date=day,
    )


def test_anchored_folds_expand_train_and_do_not_overlap_validation() -> None:
    folds = build_anchored_folds(
        data_start="2021-03-16",
        initial_validation_start="2023-01-01",
        data_end="2024-04-01",
        validation_months=6,
    )

    assert len(folds) == 3
    assert folds[0].train_start == "2021-03-16"
    assert folds[0].train_end == "2023-01-01"
    assert folds[0].validation_end == folds[1].validation_start
    assert folds[-1].validation_end == "2024-04-01"


def test_walk_forward_reports_each_validation_segment_separately() -> None:
    folds = build_anchored_folds(
        data_start="2021-01-01",
        initial_validation_start="2022-01-01",
        data_end="2023-01-01",
        validation_months=6,
    )
    trades = [
        _trade("2022-01-10", 100.0),
        _trade("2022-02-10", -50.0),
        _trade("2022-07-10", -25.0),
    ]

    results = summarize_walk_forward(trades, folds)

    assert [result.net_pnl for result in results] == [50.0, -25.0]
    assert results[0].profit_factor == 2.0
    assert results[1].max_drawdown == -25.0


def test_invalid_fold_definition_fails_closed() -> None:
    with pytest.raises(ValueError):
        build_anchored_folds(
            data_start="2023-01-01",
            initial_validation_start="2022-01-01",
            data_end="2024-01-01",
        )
