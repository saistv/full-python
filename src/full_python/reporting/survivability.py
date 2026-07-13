from __future__ import annotations

from dataclasses import asdict, dataclass
import statistics
from typing import Any, Optional

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class TradeResult:
    exit_timestamp_utc: str
    side: str
    pnl: float


@dataclass(frozen=True)
class SurvivabilityReport:
    trade_count: int
    net_pnl: float
    max_drawdown: float
    max_loss_streak: int
    pnl_without_best_trade: float
    long_pnl: float
    short_pnl: float
    win_rate: float
    expectancy_per_trade: float
    profit_factor: Optional[float]
    pnl_without_top_3_trades: float
    pnl_without_top_5_trades: float
    pnl_without_top_10_trades: float

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def build_survivability_report(trades: list[TradeResult]) -> SurvivabilityReport:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    current_loss_streak = 0
    max_loss_streak = 0
    long_pnl = 0.0
    short_pnl = 0.0

    for trade in trades:
        equity += trade.pnl
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if trade.pnl < 0:
            current_loss_streak += 1
            max_loss_streak = max(max_loss_streak, current_loss_streak)
        else:
            current_loss_streak = 0
        if trade.side == "long":
            long_pnl += trade.pnl
        elif trade.side == "short":
            short_pnl += trade.pnl

    best_trade = max((trade.pnl for trade in trades), default=0.0)
    net_pnl = sum(trade.pnl for trade in trades)
    wins = [trade.pnl for trade in trades if trade.pnl > 0]
    losses = [trade.pnl for trade in trades if trade.pnl < 0]
    ranked_wins = sorted(wins, reverse=True)

    def without_top(count: int) -> float:
        return net_pnl - sum(ranked_wins[:count])

    gross_loss = -sum(losses)
    return SurvivabilityReport(
        trade_count=len(trades),
        net_pnl=net_pnl,
        max_drawdown=max_drawdown,
        max_loss_streak=max_loss_streak,
        pnl_without_best_trade=net_pnl - best_trade,
        long_pnl=long_pnl,
        short_pnl=short_pnl,
        win_rate=(len(wins) / len(trades)) if trades else 0.0,
        expectancy_per_trade=(net_pnl / len(trades)) if trades else 0.0,
        profit_factor=(sum(wins) / gross_loss) if gross_loss > 0 else None,
        pnl_without_top_3_trades=without_top(3),
        pnl_without_top_5_trades=without_top(5),
        pnl_without_top_10_trades=without_top(10),
    )


@dataclass(frozen=True)
class DailyMetrics:
    trading_days: int
    days_with_trades: int
    profitable_days: int
    losing_days: int
    profitable_day_rate: float
    avg_daily_pnl: float
    best_day_pnl: float
    worst_day_pnl: float
    best_day_share: Optional[float]
    sharpe_annualized: float
    max_time_underwater_days: int
    pnl_without_top_1_day: float
    pnl_without_top_3_days: float
    pnl_without_top_5_days: float
    pnl_without_top_10_days: float
    top_5_day_share: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_daily_metrics(
    daily_pnl: dict[str, float],
    session_dates: list[str],
) -> DailyMetrics:
    """Daily-resolution survivability metrics.

    ``session_dates`` is the full trading calendar observed in the data;
    days without a closed trade count as zero-P&L days. Sharpe is the
    annualized mean/stdev of that full daily series — computing it on
    trade days only would overstate smoothness.
    """
    ordered_days = list(session_dates)
    for day in sorted(daily_pnl):
        if day not in ordered_days:
            ordered_days.append(day)

    series = [daily_pnl.get(day, 0.0) for day in ordered_days]
    trading_days = len(series)
    days_with_trades = sum(1 for day in ordered_days if day in daily_pnl)
    profitable_days = sum(1 for value in series if value > 0)
    losing_days = sum(1 for value in series if value < 0)
    net = sum(series)
    best_day = max(series, default=0.0)
    worst_day = min(series, default=0.0)
    ranked_positive_days = sorted((value for value in series if value > 0), reverse=True)

    def without_top(count: int) -> float:
        return net - sum(ranked_positive_days[:count])

    if trading_days >= 2 and statistics.stdev(series) > 0:
        sharpe = (
            statistics.mean(series)
            / statistics.stdev(series)
            * (TRADING_DAYS_PER_YEAR ** 0.5)
        )
    else:
        sharpe = 0.0

    equity = 0.0
    peak = 0.0
    underwater = 0
    max_underwater = 0
    for value in series:
        equity += value
        if equity >= peak:
            peak = equity
            underwater = 0
        else:
            underwater += 1
            max_underwater = max(max_underwater, underwater)

    return DailyMetrics(
        trading_days=trading_days,
        days_with_trades=days_with_trades,
        profitable_days=profitable_days,
        losing_days=losing_days,
        profitable_day_rate=(profitable_days / trading_days) if trading_days else 0.0,
        avg_daily_pnl=(net / trading_days) if trading_days else 0.0,
        best_day_pnl=best_day,
        worst_day_pnl=worst_day,
        best_day_share=(best_day / net) if net > 0 else None,
        sharpe_annualized=sharpe,
        max_time_underwater_days=max_underwater,
        pnl_without_top_1_day=without_top(1),
        pnl_without_top_3_days=without_top(3),
        pnl_without_top_5_days=without_top(5),
        pnl_without_top_10_days=without_top(10),
        top_5_day_share=(sum(ranked_positive_days[:5]) / net) if net > 0 else None,
    )


def build_monthly_breakdown(daily_pnl: dict[str, float]) -> dict[str, dict[str, float]]:
    """Aggregate daily P&L into calendar months keyed as YYYY-MM."""
    months: dict[str, dict[str, float]] = {}
    for day in sorted(daily_pnl):
        month = day[:7]
        bucket = months.setdefault(month, {"net_pnl": 0.0, "days_with_trades": 0})
        bucket["net_pnl"] += daily_pnl[day]
        bucket["days_with_trades"] += 1
    return months
