"""Finite-horizon path risk for a capped live pilot."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import random
from typing import Sequence

from full_python.reporting.bootstrap import Interval


@dataclass(frozen=True)
class PilotPathReport:
    horizon_sessions: int
    loss_budget: float
    income_target: float
    block_length_sessions: int
    draws: int
    seed: int
    ending_pnl_95: Interval
    probability_loss_budget_breached: float
    probability_positive_end: float
    probability_income_target_met: float
    max_drawdown_median: float
    max_drawdown_p95_adverse: float
    max_drawdown_p99_adverse: float
    minimum_equity_median: float
    minimum_equity_p95_adverse: float
    minimum_equity_p99_adverse: float
    observed_window_count: int
    observed_window_loss_budget_breach_rate: float
    observed_max_drawdown_worst: float
    observed_minimum_equity_worst: float
    observed_ending_pnl_min: float
    observed_ending_pnl_max: float

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["ending_pnl_95"] = self.ending_pnl_95.to_dict()
        return result


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _path_metrics(
    path: Sequence[float], loss_budget: float
) -> tuple[float, float, float, bool]:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    minimum_equity = 0.0
    breached = False
    for pnl in path:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
        minimum_equity = min(minimum_equity, equity)
        breached = breached or equity <= -loss_budget
    return equity, drawdown, minimum_equity, breached


def _sample_horizon(
    series: Sequence[float], *, horizon: int, block_length: int, rng: random.Random
) -> list[float]:
    block = min(block_length, len(series))
    max_start = len(series) - block
    sample: list[float] = []
    while len(sample) < horizon:
        start = rng.randint(0, max_start)
        sample.extend(series[start:start + block])
    return sample[:horizon]


def build_pilot_path_report(
    daily_series: Sequence[float],
    *,
    horizon_sessions: int = 30,
    loss_budget: float = 500.0,
    income_target: float = 5000.0,
    block_length_sessions: int = 10,
    draws: int = 10000,
    seed: int = 20260713,
) -> PilotPathReport:
    series = [float(value) for value in daily_series]
    if not series:
        raise ValueError("daily_series is required")
    if horizon_sessions < 1:
        raise ValueError("horizon_sessions must be positive")
    if loss_budget <= 0:
        raise ValueError("loss_budget must be positive")
    if block_length_sessions < 1:
        raise ValueError("block_length_sessions must be positive")
    if draws < 1:
        raise ValueError("draws must be positive")

    rng = random.Random(seed)
    endings: list[float] = []
    drawdowns: list[float] = []
    minimum_equities: list[float] = []
    breaches = 0
    positive = 0
    target_met = 0
    for _ in range(draws):
        path = _sample_horizon(
            series,
            horizon=horizon_sessions,
            block_length=block_length_sessions,
            rng=rng,
        )
        ending, drawdown, minimum_equity, breached = _path_metrics(path, loss_budget)
        endings.append(ending)
        drawdowns.append(drawdown)
        minimum_equities.append(minimum_equity)
        breaches += int(breached)
        positive += int(ending > 0)
        target_met += int(ending >= income_target)

    if len(series) >= horizon_sessions:
        windows = [
            series[index:index + horizon_sessions]
            for index in range(len(series) - horizon_sessions + 1)
        ]
    else:
        windows = [series]
    observed = [_path_metrics(window, loss_budget) for window in windows]
    observed_endings = [item[0] for item in observed]
    observed_drawdowns = [item[1] for item in observed]
    observed_minimum_equities = [item[2] for item in observed]

    return PilotPathReport(
        horizon_sessions=horizon_sessions,
        loss_budget=loss_budget,
        income_target=income_target,
        block_length_sessions=min(block_length_sessions, len(series)),
        draws=draws,
        seed=seed,
        ending_pnl_95=Interval(
            _quantile(endings, 0.025),
            _quantile(endings, 0.5),
            _quantile(endings, 0.975),
        ),
        probability_loss_budget_breached=breaches / draws,
        probability_positive_end=positive / draws,
        probability_income_target_met=target_met / draws,
        max_drawdown_median=_quantile(drawdowns, 0.5),
        max_drawdown_p95_adverse=_quantile(drawdowns, 0.05),
        max_drawdown_p99_adverse=_quantile(drawdowns, 0.01),
        minimum_equity_median=_quantile(minimum_equities, 0.5),
        minimum_equity_p95_adverse=_quantile(minimum_equities, 0.05),
        minimum_equity_p99_adverse=_quantile(minimum_equities, 0.01),
        observed_window_count=len(windows),
        observed_window_loss_budget_breach_rate=(
            sum(item[3] for item in observed) / len(observed)
        ),
        observed_max_drawdown_worst=min(observed_drawdowns),
        observed_minimum_equity_worst=min(observed_minimum_equities),
        observed_ending_pnl_min=min(observed_endings),
        observed_ending_pnl_max=max(observed_endings),
    )
