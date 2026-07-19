"""Multiplicity-aware Sharpe confidence without optional SciPy dependencies.

IID PSR is diagnostic. DSR is computed only from observed related-trial Sharpe
dispersion plus a defensible effective independent-trial count. The preregistered
trial budget is governance metadata, never a DSR input.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import statistics
from statistics import NormalDist
from typing import Iterable, Optional


_EULER_MASCHERONI = 0.5772156649015329


@dataclass(frozen=True)
class SharpeConfidence:
    observation_count: int
    daily_sharpe: Optional[float]
    annualized_sharpe: Optional[float]
    skewness: Optional[float]
    kurtosis: Optional[float]
    iid_psr_probability_sharpe_above_zero: Optional[float]
    iid_psr_status: str
    candidate_family_trial_budget: int
    dsr_effective_independent_trials: Optional[int]
    dsr_cross_trial_sharpe_std_daily: Optional[float]
    dsr_benchmark_daily: Optional[float]
    dsr_benchmark_annualized: Optional[float]
    dsr_probability: Optional[float]
    dsr_status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _probabilistic_sharpe(
    *,
    sharpe: float,
    benchmark: float,
    observations: int,
    skewness: float,
    kurtosis: float,
) -> Optional[float]:
    variance_term = (
        1.0
        - skewness * sharpe
        + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe
    )
    if observations < 3 or variance_term <= 0 or not math.isfinite(variance_term):
        return None
    z_score = (
        (sharpe - benchmark)
        * math.sqrt(observations - 1)
        / math.sqrt(variance_term)
    )
    return NormalDist().cdf(z_score)


def _expected_maximum_sharpe(
    *, cross_trial_sharpe_std: Optional[float], trial_count: int
) -> Optional[float]:
    if trial_count == 1:
        return 0.0
    if cross_trial_sharpe_std is None:
        return None
    normal = NormalDist()
    return cross_trial_sharpe_std * (
        (1.0 - _EULER_MASCHERONI) * normal.inv_cdf(1.0 - 1.0 / trial_count)
        + _EULER_MASCHERONI
        * normal.inv_cdf(1.0 - 1.0 / (trial_count * math.e))
    )


def build_sharpe_confidence(
    daily_returns: Iterable[float],
    *,
    candidate_family_trial_budget: int,
    annualization_sessions: int = 252,
    related_trial_daily_sharpes: Optional[Iterable[float]] = None,
    effective_independent_trials: Optional[int] = None,
) -> SharpeConfidence:
    """Build IID PSR and, when supported by trial data, canonical DSR inputs.

    `daily_returns` must include zero-return score sessions. IID PSR is disclosed
    as diagnostic because serial dependence can overstate its confidence. DSR is
    computed only when the caller supplies the observed daily Sharpe values from
    at least two related trials and a defensible effective independent-trial count.
    A trial count alone is deliberately insufficient.
    """
    values = [float(value) for value in daily_returns]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("daily_returns must all be finite")
    count = len(values)
    if (
        isinstance(candidate_family_trial_budget, bool)
        or not isinstance(candidate_family_trial_budget, int)
        or candidate_family_trial_budget < 1
    ):
        raise ValueError("candidate_family_trial_budget must be a positive integer")
    if (
        isinstance(annualization_sessions, bool)
        or not isinstance(annualization_sessions, int)
        or annualization_sessions < 1
    ):
        raise ValueError("annualization_sessions must be a positive integer")
    related = (
        None
        if related_trial_daily_sharpes is None
        else [float(value) for value in related_trial_daily_sharpes]
    )
    if (related is None) != (effective_independent_trials is None):
        raise ValueError(
            "related trial Sharpes and effective_independent_trials must be supplied together"
        )
    if related is not None:
        if len(related) < 2 or any(not math.isfinite(value) for value in related):
            raise ValueError("DSR requires at least two finite related trial Sharpes")
        if (
            isinstance(effective_independent_trials, bool)
            or not isinstance(effective_independent_trials, int)
            or not 2 <= effective_independent_trials <= len(related)
        ):
            raise ValueError(
                "effective_independent_trials must be an integer in [2, trial count]"
            )

    def unavailable_result() -> SharpeConfidence:
        return SharpeConfidence(
            observation_count=count,
            daily_sharpe=None,
            annualized_sharpe=None,
            skewness=None,
            kurtosis=None,
            iid_psr_probability_sharpe_above_zero=None,
            iid_psr_status="unavailable_insufficient_return_variation",
            candidate_family_trial_budget=candidate_family_trial_budget,
            dsr_effective_independent_trials=effective_independent_trials,
            dsr_cross_trial_sharpe_std_daily=(
                statistics.stdev(related) if related is not None else None
            ),
            dsr_benchmark_daily=None,
            dsr_benchmark_annualized=None,
            dsr_probability=None,
            dsr_status=(
                "unavailable_candidate_sharpe"
                if related is not None
                else "unavailable_insufficient_cross_trial_data"
            ),
        )

    if count < 3:
        return unavailable_result()

    mean = sum(values) / count
    centered = [value - mean for value in values]
    sample_variance = sum(value * value for value in centered) / (count - 1)
    if sample_variance <= 0 or not math.isfinite(sample_variance):
        return unavailable_result()

    deviation = math.sqrt(sample_variance)
    daily_sharpe = mean / deviation
    population_m2 = sum(value * value for value in centered) / count
    population_scale = math.sqrt(population_m2)
    skewness = (
        sum(value**3 for value in centered) / count / population_scale**3
        if population_scale > 0
        else 0.0
    )
    kurtosis = (
        sum(value**4 for value in centered) / count / population_scale**4
        if population_scale > 0
        else 3.0
    )
    psr = _probabilistic_sharpe(
        sharpe=daily_sharpe,
        benchmark=0.0,
        observations=count,
        skewness=skewness,
        kurtosis=kurtosis,
    )

    cross_trial_std = statistics.stdev(related) if related is not None else None
    benchmark = (
        _expected_maximum_sharpe(
            cross_trial_sharpe_std=cross_trial_std,
            trial_count=effective_independent_trials,
        )
        if effective_independent_trials is not None
        else None
    )
    dsr = (
        _probabilistic_sharpe(
            sharpe=daily_sharpe,
            benchmark=benchmark,
            observations=count,
            skewness=skewness,
            kurtosis=kurtosis,
        )
        if benchmark is not None
        else None
    )
    annualization = math.sqrt(annualization_sessions)
    return SharpeConfidence(
        observation_count=count,
        daily_sharpe=daily_sharpe,
        annualized_sharpe=daily_sharpe * annualization,
        skewness=skewness,
        kurtosis=kurtosis,
        iid_psr_probability_sharpe_above_zero=psr,
        iid_psr_status="diagnostic_only_serial_dependence_not_adjusted",
        candidate_family_trial_budget=candidate_family_trial_budget,
        dsr_effective_independent_trials=effective_independent_trials,
        dsr_cross_trial_sharpe_std_daily=cross_trial_std,
        dsr_benchmark_daily=benchmark,
        dsr_benchmark_annualized=(
            benchmark * annualization if benchmark is not None else None
        ),
        dsr_probability=dsr,
        dsr_status=(
            "available_cross_trial_dispersion"
            if benchmark is not None
            else "unavailable_insufficient_cross_trial_data"
        ),
    )
