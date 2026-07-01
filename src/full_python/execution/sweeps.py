from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from full_python.execution.simulator import (
    ExitConversionConfig,
    ReentryControlConfig,
    SimulationCosts,
    simulate_strategy_trades,
)
from full_python.models import MarketBar
from full_python.strategy.baseline import BaselineMomentumStrategy
from full_python.strategy.config import BaselineMomentumConfig


@dataclass(frozen=True)
class ExitSweepConfig:
    mfe_trailing_activation_points: Sequence[float]
    mfe_trailing_giveback_points: Sequence[float]
    fresh_breakout_clearance_points: Sequence[float]
    cooldown_bars_after_exit: Sequence[int]
    point_value: float = 2.0
    slippage_points_per_side: float = 1.0
    commission_per_contract: float = 1.0
    symbol_change_exit_mode: str = "previous_close"
    enable_long: bool = True
    enable_short: bool = False


def run_exit_sweep(
    bars: Iterable[MarketBar],
    config: ExitSweepConfig,
) -> dict[str, Any]:
    bar_list = list(bars)
    results: list[dict[str, Any]] = []
    for activation in config.mfe_trailing_activation_points:
        for giveback in config.mfe_trailing_giveback_points:
            for clearance in config.fresh_breakout_clearance_points:
                for cooldown in config.cooldown_bars_after_exit:
                    result = _run_one_combo(
                        bar_list,
                        config=config,
                        activation=activation,
                        giveback=giveback,
                        clearance=clearance,
                        cooldown=cooldown,
                    )
                    results.append(result)

    ranked_results = sorted(
        results,
        key=lambda result: (
            result["total_net_pnl_dollars"],
            result["pnl_without_best_5_trades"],
            -abs(result["max_drawdown_dollars"]),
        ),
        reverse=True,
    )
    return {
        "combo_count": len(ranked_results),
        "ranking": "total_net_pnl_dollars_desc_then_robustness",
        "results": ranked_results,
    }


def _run_one_combo(
    bars: list[MarketBar],
    *,
    config: ExitSweepConfig,
    activation: float,
    giveback: float,
    clearance: float,
    cooldown: int,
) -> dict[str, Any]:
    ledger = simulate_strategy_trades(
        bars,
        BaselineMomentumStrategy(
            BaselineMomentumConfig(
                enable_long=config.enable_long,
                enable_short=config.enable_short,
            )
        ),
        costs=SimulationCosts(
            point_value=config.point_value,
            slippage_points_per_side=config.slippage_points_per_side,
            commission_per_contract=config.commission_per_contract,
        ),
        symbol_change_exit_mode=config.symbol_change_exit_mode,
        exit_conversion=ExitConversionConfig(
            mfe_trailing_activation_points=activation,
            mfe_trailing_giveback_points=giveback,
        ),
        reentry_control=ReentryControlConfig(
            cooldown_bars_after_exit=cooldown,
            require_fresh_breakout_after_exit=True,
            fresh_breakout_clearance_points=clearance,
        ),
    )
    net_pnls = [trade.net_pnl_dollars for trade in ledger.trades]
    summary = ledger.summary()
    return {
        "mfe_trailing_activation_points": activation,
        "mfe_trailing_giveback_points": giveback,
        "fresh_breakout_clearance_points": clearance,
        "cooldown_bars_after_exit": cooldown,
        "trade_count": summary["trade_count"],
        "win_rate": summary["win_rate"],
        "total_net_pnl_dollars": summary["total_net_pnl_dollars"],
        "max_drawdown_dollars": _max_drawdown(net_pnls),
        "max_loss_streak": _max_loss_streak(net_pnls),
        "pnl_without_best_5_trades": _pnl_without_best(net_pnls, 5),
        "exit_reason_counts": summary["exit_reason_counts"],
    }


def _max_drawdown(net_pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in net_pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return max_drawdown


def _max_loss_streak(net_pnls: list[float]) -> int:
    current_streak = 0
    max_streak = 0
    for pnl in net_pnls:
        if pnl < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def _pnl_without_best(net_pnls: list[float], count: int) -> float:
    return sum(net_pnls) - sum(sorted(net_pnls, reverse=True)[:count])
