"""Trade-level expectancy, R-multiple, streak, and exit-reason metrics.

Complements reporting/survivability.py (drawdown, loss-streak, top-trade
dependency) with the risk-normalized metrics Gate 1 needs to compare
candidates that shift average win/loss size, not just net P&L. No new
field is added to Trade: Adaptive Trend's stop is frozen at entry and
never revised (strategy/adaptive_trend.py), so Trade.stop_price already
IS the initial stop, and initial risk is derived, not stored.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import statistics
from typing import Any, Iterable, Optional

from full_python.models import Trade


def initial_risk_points(trade: Trade) -> float:
    return abs(trade.entry_price - trade.stop_price)


def r_multiple(trade: Trade, point_value: float) -> Optional[float]:
    risk_dollars = initial_risk_points(trade) * point_value * trade.quantity
    if risk_dollars <= 0:
        return None
    return trade.net_pnl / risk_dollars


@dataclass(frozen=True)
class ExpectancyReport:
    trade_count: int
    win_count: int
    loss_count: int
    scratch_count: int
    win_rate: float
    avg_win_dollars: float
    avg_loss_dollars: float
    expectancy_dollars: float
    avg_r_multiple: Optional[float]
    median_r_multiple: Optional[float]
    r_multiples_computed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_expectancy_report(trades: Iterable[Trade], *, point_value: float) -> ExpectancyReport:
    trades = list(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    scratches = [t for t in trades if t.net_pnl == 0]
    r_values = [r for r in (r_multiple(t, point_value) for t in trades) if r is not None]

    return ExpectancyReport(
        trade_count=len(trades),
        win_count=len(wins),
        loss_count=len(losses),
        scratch_count=len(scratches),
        win_rate=(len(wins) / len(trades)) if trades else 0.0,
        avg_win_dollars=(sum(t.net_pnl for t in wins) / len(wins)) if wins else 0.0,
        avg_loss_dollars=(abs(sum(t.net_pnl for t in losses)) / len(losses)) if losses else 0.0,
        expectancy_dollars=(sum(t.net_pnl for t in trades) / len(trades)) if trades else 0.0,
        avg_r_multiple=(sum(r_values) / len(r_values)) if r_values else None,
        median_r_multiple=(statistics.median(r_values)) if r_values else None,
        r_multiples_computed=len(r_values),
    )


@dataclass(frozen=True)
class ExitReasonBucket:
    exit_reason: str
    trade_count: int
    net_pnl: float
    win_rate: float
    avg_r_multiple: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_exit_reason_breakdown(
    trades: Iterable[Trade], *, point_value: float
) -> list[ExitReasonBucket]:
    buckets: dict[str, list[Trade]] = {}
    for trade in trades:
        buckets.setdefault(trade.exit_reason, []).append(trade)

    result = []
    for reason, group in buckets.items():
        wins = [t for t in group if t.net_pnl > 0]
        r_values = [r for r in (r_multiple(t, point_value) for t in group) if r is not None]
        result.append(
            ExitReasonBucket(
                exit_reason=reason,
                trade_count=len(group),
                net_pnl=sum(t.net_pnl for t in group),
                win_rate=(len(wins) / len(group)) if group else 0.0,
                avg_r_multiple=(sum(r_values) / len(r_values)) if r_values else None,
            )
        )
    result.sort(key=lambda bucket: bucket.exit_reason)
    return result


def max_win_streak(trades: Iterable[Trade]) -> int:
    current = 0
    best = 0
    for trade in trades:
        if trade.net_pnl > 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best
