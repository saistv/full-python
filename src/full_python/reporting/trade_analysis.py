from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AnalyzedTrade:
    trade_id: str
    symbol: str
    side: str
    exit_reason: str
    entry_timestamp_utc: str
    exit_timestamp_utc: str
    pnl_points: float
    gross_pnl_dollars: float
    commission_dollars: float
    net_pnl_dollars: float
    max_favorable_excursion_points: float
    max_adverse_excursion_points: float


def load_trade_csv(path: str | Path) -> list[AnalyzedTrade]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
        return [_trade_from_row(row) for row in rows]


def build_trade_analysis(trades: list[AnalyzedTrade]) -> dict[str, Any]:
    total_net_pnl = sum(trade.net_pnl_dollars for trade in trades)
    winning_trades = sum(1 for trade in trades if trade.net_pnl_dollars > 0)
    losing_trades = sum(1 for trade in trades if trade.net_pnl_dollars < 0)
    flat_trades = len(trades) - winning_trades - losing_trades

    return {
        "summary": {
            "trade_count": len(trades),
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "flat_trades": flat_trades,
            "win_rate": winning_trades / len(trades) if trades else 0.0,
            "total_pnl_points": sum(trade.pnl_points for trade in trades),
            "total_gross_pnl_dollars": sum(trade.gross_pnl_dollars for trade in trades),
            "total_commission_dollars": sum(trade.commission_dollars for trade in trades),
            "total_net_pnl_dollars": total_net_pnl,
            "average_net_pnl_dollars": total_net_pnl / len(trades) if trades else 0.0,
        },
        "risk": _build_risk(trades),
        "top_trade_dependency": _build_top_trade_dependency(trades),
        "monthly_breakdown": _build_period_breakdown(trades, "month"),
        "quarterly_breakdown": _build_period_breakdown(trades, "quarter"),
        "exit_reason_breakdown": _build_category_breakdown(trades, lambda trade: trade.exit_reason),
        "symbol_breakdown": _build_category_breakdown(trades, lambda trade: trade.symbol),
        "side_breakdown": _build_category_breakdown(trades, lambda trade: trade.side),
        "stopped_trade_excursion": _build_stopped_trade_excursion(trades),
    }


def write_trade_analysis_json(analysis: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _trade_from_row(row: dict[str, str]) -> AnalyzedTrade:
    return AnalyzedTrade(
        trade_id=row["trade_id"],
        symbol=row["symbol"],
        side=row["side"],
        exit_reason=row["exit_reason"],
        entry_timestamp_utc=row["entry_timestamp_utc"],
        exit_timestamp_utc=row["exit_timestamp_utc"],
        pnl_points=float(row["pnl_points"]),
        gross_pnl_dollars=float(row["gross_pnl_dollars"]),
        commission_dollars=float(row["commission_dollars"]),
        net_pnl_dollars=float(row["net_pnl_dollars"]),
        max_favorable_excursion_points=float(row.get("max_favorable_excursion_points") or 0.0),
        max_adverse_excursion_points=float(row.get("max_adverse_excursion_points") or 0.0),
    )


def _build_risk(trades: list[AnalyzedTrade]) -> dict[str, float | int]:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    current_loss_streak = 0
    max_loss_streak = 0
    for trade in trades:
        equity += trade.net_pnl_dollars
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if trade.net_pnl_dollars < 0:
            current_loss_streak += 1
            max_loss_streak = max(max_loss_streak, current_loss_streak)
        else:
            current_loss_streak = 0
    return {
        "max_drawdown_dollars": max_drawdown,
        "max_loss_streak": max_loss_streak,
        "ending_equity_dollars": equity,
    }


def _build_top_trade_dependency(trades: list[AnalyzedTrade]) -> dict[str, float]:
    total_net_pnl = sum(trade.net_pnl_dollars for trade in trades)
    best_pnls = sorted((trade.net_pnl_dollars for trade in trades), reverse=True)
    dependency = {
        "best_trade_net_pnl_dollars": best_pnls[0] if best_pnls else 0.0,
    }
    for count in (1, 3, 5):
        dependency[f"pnl_without_best_{count}_trades"] = total_net_pnl - sum(best_pnls[:count])
    return dependency


def _build_stopped_trade_excursion(trades: list[AnalyzedTrade]) -> dict[str, float | int]:
    stopped_trades = [trade for trade in trades if trade.exit_reason == "stop"]
    if not stopped_trades:
        return {
            "trade_count": 0,
            "average_mfe_points": 0.0,
            "max_mfe_points": 0.0,
            "average_mae_points": 0.0,
            "min_mae_points": 0.0,
        }
    total_mfe = sum(trade.max_favorable_excursion_points for trade in stopped_trades)
    total_mae = sum(trade.max_adverse_excursion_points for trade in stopped_trades)
    return {
        "trade_count": len(stopped_trades),
        "average_mfe_points": total_mfe / len(stopped_trades),
        "max_mfe_points": max(trade.max_favorable_excursion_points for trade in stopped_trades),
        "average_mae_points": total_mae / len(stopped_trades),
        "min_mae_points": min(trade.max_adverse_excursion_points for trade in stopped_trades),
    }


def _build_period_breakdown(trades: list[AnalyzedTrade], period: str) -> dict[str, dict[str, float | int]]:
    breakdown: dict[str, dict[str, float | int]] = {}
    for trade in trades:
        period_key = _period_key(trade.exit_timestamp_utc, period)
        _add_to_breakdown(breakdown, period_key, trade)
    return breakdown


def _build_category_breakdown(
    trades: list[AnalyzedTrade],
    key_builder: Any,
) -> dict[str, dict[str, float | int]]:
    breakdown: dict[str, dict[str, float | int]] = {}
    for trade in trades:
        _add_to_breakdown(breakdown, str(key_builder(trade)), trade)
    return breakdown


def _add_to_breakdown(
    breakdown: dict[str, dict[str, float | int]],
    key: str,
    trade: AnalyzedTrade,
) -> None:
    bucket = breakdown.setdefault(
        key,
        {
            "trade_count": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "flat_trades": 0,
            "net_pnl_dollars": 0.0,
            "pnl_points": 0.0,
        },
    )
    bucket["trade_count"] += 1
    bucket["net_pnl_dollars"] += trade.net_pnl_dollars
    bucket["pnl_points"] += trade.pnl_points
    if trade.net_pnl_dollars > 0:
        bucket["winning_trades"] += 1
    elif trade.net_pnl_dollars < 0:
        bucket["losing_trades"] += 1
    else:
        bucket["flat_trades"] += 1


def _period_key(timestamp_utc: str, period: str) -> str:
    parsed = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    if period == "month":
        return f"{parsed.year:04d}-{parsed.month:02d}"
    if period == "quarter":
        quarter = ((parsed.month - 1) // 3) + 1
        return f"{parsed.year:04d}-Q{quarter}"
    raise ValueError(f"Unsupported period: {period}")
