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
    return SurvivabilityReport(
        trade_count=len(trades),
        net_pnl=net_pnl,
        max_drawdown=max_drawdown,
        max_loss_streak=max_loss_streak,
        pnl_without_best_trade=net_pnl - best_trade,
        long_pnl=long_pnl,
        short_pnl=short_pnl,
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
