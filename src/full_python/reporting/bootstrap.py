"""Deterministic moving-block bootstrap for session-level strategy risk."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import random
import statistics
from typing import Any, Sequence

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class Interval:
    lower: float
    median: float
    upper: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class BlockBootstrapReport:
    session_count: int
    block_length_sessions: int
    draws: int
    seed: int
    total_net_pnl_95: Interval
    annualized_net_pnl_95: Interval
    sharpe_annualized_95: Interval
    max_drawdown_median: float
    max_drawdown_p95_adverse: float
    max_drawdown_p99_adverse: float
    max_losing_day_streak_p95: float
    probability_total_net_nonpositive: float

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["total_net_pnl_95"] = self.total_net_pnl_95.to_dict()
        result["annualized_net_pnl_95"] = self.annualized_net_pnl_95.to_dict()
        result["sharpe_annualized_95"] = self.sharpe_annualized_95.to_dict()
        return result


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _max_drawdown(series: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for pnl in series:
        equity += pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _max_losing_streak(series: Sequence[float]) -> int:
    current = 0
    worst = 0
    for pnl in series:
        if pnl < 0:
            current += 1
            worst = max(worst, current)
        else:
            current = 0
    return worst


def _annualized_sharpe(series: Sequence[float]) -> float:
    if len(series) < 2:
        return 0.0
    deviation = statistics.stdev(series)
    if deviation == 0:
        return 0.0
    return statistics.mean(series) / deviation * math.sqrt(TRADING_DAYS_PER_YEAR)


def _sample_blocks(
    series: Sequence[float], *, block_length: int, rng: random.Random
) -> list[float]:
    size = len(series)
    block = min(block_length, size)
    sampled: list[float] = []
    max_start = size - block
    while len(sampled) < size:
        start = rng.randint(0, max_start)
        sampled.extend(series[start:start + block])
    return sampled[:size]


def build_block_bootstrap_report(
    daily_series: Sequence[float],
    *,
    block_length_sessions: int = 10,
    draws: int = 2000,
    seed: int = 20260712,
) -> BlockBootstrapReport:
    if block_length_sessions < 1:
        raise ValueError("block_length_sessions must be positive")
    if draws < 1:
        raise ValueError("draws must be positive")
    series = [float(value) for value in daily_series]
    if not series:
        zero = Interval(0.0, 0.0, 0.0)
        return BlockBootstrapReport(
            session_count=0,
            block_length_sessions=block_length_sessions,
            draws=draws,
            seed=seed,
            total_net_pnl_95=zero,
            annualized_net_pnl_95=zero,
            sharpe_annualized_95=zero,
            max_drawdown_median=0.0,
            max_drawdown_p95_adverse=0.0,
            max_drawdown_p99_adverse=0.0,
            max_losing_day_streak_p95=0.0,
            # With no observations there is no evidence of positive profit.
            probability_total_net_nonpositive=1.0,
        )

    rng = random.Random(seed)
    totals: list[float] = []
    annualized: list[float] = []
    sharpes: list[float] = []
    drawdowns: list[float] = []
    streaks: list[float] = []
    annualization = TRADING_DAYS_PER_YEAR / len(series)
    for _ in range(draws):
        sample = _sample_blocks(
            series, block_length=block_length_sessions, rng=rng
        )
        total = sum(sample)
        totals.append(total)
        annualized.append(total * annualization)
        sharpes.append(_annualized_sharpe(sample))
        drawdowns.append(_max_drawdown(sample))
        streaks.append(float(_max_losing_streak(sample)))

    return BlockBootstrapReport(
        session_count=len(series),
        block_length_sessions=min(block_length_sessions, len(series)),
        draws=draws,
        seed=seed,
        total_net_pnl_95=Interval(
            _quantile(totals, 0.025), _quantile(totals, 0.5), _quantile(totals, 0.975)
        ),
        annualized_net_pnl_95=Interval(
            _quantile(annualized, 0.025),
            _quantile(annualized, 0.5),
            _quantile(annualized, 0.975),
        ),
        sharpe_annualized_95=Interval(
            _quantile(sharpes, 0.025),
            _quantile(sharpes, 0.5),
            _quantile(sharpes, 0.975),
        ),
        max_drawdown_median=_quantile(drawdowns, 0.5),
        max_drawdown_p95_adverse=_quantile(drawdowns, 0.05),
        max_drawdown_p99_adverse=_quantile(drawdowns, 0.01),
        max_losing_day_streak_p95=_quantile(streaks, 0.95),
        probability_total_net_nonpositive=(
            sum(total <= 0 for total in totals) / len(totals)
        ),
    )
