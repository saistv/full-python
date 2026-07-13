"""Anchored walk-forward folds and per-fold trade reporting."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Sequence

from full_python.models import Trade


def _parse_day(value: str) -> date:
    return date.fromisoformat(value[:10])


def _add_months(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 + months
    year, month_zero = divmod(total, 12)
    month = month_zero + 1
    month_lengths = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return date(year, month, min(day.day, month_lengths[month - 1]))


@dataclass(frozen=True)
class AnchoredFold:
    fold_index: int
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FoldResult:
    fold: AnchoredFold
    trade_count: int
    net_pnl: float
    profit_factor: float | None
    max_drawdown: float
    win_rate: float

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["fold"] = self.fold.to_dict()
        return result


def build_anchored_folds(
    *,
    data_start: str,
    initial_validation_start: str,
    data_end: str,
    validation_months: int = 6,
) -> tuple[AnchoredFold, ...]:
    if validation_months < 1:
        raise ValueError("validation_months must be positive")
    start = _parse_day(data_start)
    validation_start = _parse_day(initial_validation_start)
    end = _parse_day(data_end)
    if not start < validation_start < end:
        raise ValueError("require data_start < initial_validation_start < data_end")
    folds = []
    index = 1
    while validation_start < end:
        validation_end = min(_add_months(validation_start, validation_months), end)
        folds.append(AnchoredFold(
            fold_index=index,
            train_start=start.isoformat(),
            train_end=validation_start.isoformat(),
            validation_start=validation_start.isoformat(),
            validation_end=validation_end.isoformat(),
        ))
        validation_start = validation_end
        index += 1
    return tuple(folds)


def _max_drawdown(trades: Sequence[Trade]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for trade in trades:
        equity += trade.net_pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def summarize_walk_forward(
    trades: Sequence[Trade], folds: Sequence[AnchoredFold]
) -> tuple[FoldResult, ...]:
    results = []
    for fold in folds:
        selected = [
            trade for trade in trades
            if fold.validation_start <= trade.entry_timestamp_utc[:10] < fold.validation_end
        ]
        wins = [trade.net_pnl for trade in selected if trade.net_pnl > 0]
        losses = [trade.net_pnl for trade in selected if trade.net_pnl < 0]
        gross_loss = -sum(losses)
        results.append(FoldResult(
            fold=fold,
            trade_count=len(selected),
            net_pnl=sum(trade.net_pnl for trade in selected),
            profit_factor=(sum(wins) / gross_loss) if gross_loss > 0 else None,
            max_drawdown=_max_drawdown(selected),
            win_rate=(len(wins) / len(selected)) if selected else 0.0,
        ))
    return tuple(results)
