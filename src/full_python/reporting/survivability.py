from __future__ import annotations

from dataclasses import asdict, dataclass


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
