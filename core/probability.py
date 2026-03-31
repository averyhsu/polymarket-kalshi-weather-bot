"""Probability engine for Kalshi temperature buckets."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


EULER_GAMMA = 0.5772


@dataclass(frozen=True)
class ProbabilityResult:
    """Probability output for a Kalshi bucket."""

    p_bucket_yes: float
    p_bucket_no: float
    boundary_mass: float
    disagreement: float
    debug: Dict[str, float]


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _gumbel_cdf(x: float, mu: float, beta: float) -> float:
    if x == float("-inf"):
        return 0.0
    if x == float("inf"):
        return 1.0
    z = -(x - mu) / beta
    z = max(-60.0, min(60.0, z))
    return math.exp(-math.exp(z))


def _empirical_bucket_probability(members: List[float], bucket_low: float, bucket_high: float) -> float:
    if not members:
        return 0.5
    inside = 0
    for member in members:
        if bucket_low <= member < bucket_high:
            inside += 1
    return inside / len(members)


def _boundary_mass(mu: float, beta: float, bucket_low: float, bucket_high: float, boundary_buffer_f: float) -> float:
    segments = []
    if bucket_low != float("-inf"):
        segments.append((bucket_low, min(bucket_high, bucket_low + boundary_buffer_f)))
    if bucket_high != float("inf"):
        segments.append((max(bucket_low, bucket_high - boundary_buffer_f), bucket_high))

    if not segments:
        return 0.0

    segments.sort(key=lambda item: item[0])
    merged = []
    for start, end in segments:
        if start >= end:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    total = 0.0
    for start, end in merged:
        total += _gumbel_cdf(end, mu, beta) - _gumbel_cdf(start, mu, beta)
    return _clamp(total)


def _validate_inputs(
    mean_temp_f: Optional[float],
    sigma_temp_f: Optional[float],
    bucket_low: float,
    bucket_high: float,
    p_climo: float,
) -> None:
    if mean_temp_f is None or not math.isfinite(mean_temp_f):
        raise ValueError("mean_temp_f must be a finite number")
    if sigma_temp_f is None or sigma_temp_f < 0 or not math.isfinite(sigma_temp_f):
        raise ValueError("sigma_temp_f must be a finite non-negative number")
    if bucket_low >= bucket_high:
        raise ValueError("bucket_low must be strictly less than bucket_high")
    if not 0.0 <= p_climo <= 1.0:
        raise ValueError("p_climo must be between 0 and 1")


def estimate_bucket_probability(
    *,
    mean_temp_f: Optional[float],
    sigma_temp_f: Optional[float],
    bucket_low: float,
    bucket_high: float,
    p_climo: float,
    member_highs: Optional[Iterable[float]] = None,
    pseudo_count: float = 8.0,
    boundary_buffer_f: float = 1.0,
    min_sigma_f: float = 0.5,
) -> ProbabilityResult:
    """Estimate the probability that the final daily high settles inside a bucket."""

    members = [float(value) for value in (member_highs or []) if math.isfinite(float(value))]
    if mean_temp_f is None and members:
        mean_temp_f = sum(members) / len(members)
    if sigma_temp_f is None and len(members) > 1:
        mean = sum(members) / len(members)
        sigma_temp_f = math.sqrt(sum((member - mean) ** 2 for member in members) / len(members))

    if mean_temp_f is None or sigma_temp_f is None:
        return ProbabilityResult(
            p_bucket_yes=_clamp(p_climo),
            p_bucket_no=_clamp(1.0 - p_climo),
            boundary_mass=0.0,
            disagreement=0.0,
            debug={
                "mean_temp_f": float(mean_temp_f or 0.0),
                "sigma_temp_f": float(sigma_temp_f or 0.0),
                "alpha": 0.0,
                "p_model": _clamp(p_climo),
                "p_climo": _clamp(p_climo),
                "member_count": float(len(members)),
            },
        )

    _validate_inputs(mean_temp_f, sigma_temp_f, bucket_low, bucket_high, p_climo)
    sigma = max(float(sigma_temp_f), float(min_sigma_f))
    beta = max(sigma * math.sqrt(6.0) / math.pi, 1e-6)
    mu = float(mean_temp_f) - EULER_GAMMA * beta

    lower_cdf = _gumbel_cdf(bucket_low, mu, beta)
    upper_cdf = _gumbel_cdf(bucket_high, mu, beta)
    p_model = _clamp(upper_cdf - lower_cdf)

    count = len(members)
    alpha = count / (count + pseudo_count) if count > 0 else 0.0
    p_final = _clamp(alpha * p_model + (1.0 - alpha) * _clamp(p_climo))

    empirical = _empirical_bucket_probability(members, bucket_low, bucket_high) if members else p_model
    disagreement = _clamp(4.0 * empirical * (1.0 - empirical))
    boundary_mass = _boundary_mass(mu, beta, bucket_low, bucket_high, boundary_buffer_f)

    return ProbabilityResult(
        p_bucket_yes=p_final,
        p_bucket_no=_clamp(1.0 - p_final),
        boundary_mass=boundary_mass,
        disagreement=disagreement,
        debug={
            "mean_temp_f": float(mean_temp_f),
            "sigma_temp_f": sigma,
            "alpha": alpha,
            "p_model": p_model,
            "p_climo": _clamp(p_climo),
            "p_empirical": empirical,
            "member_count": float(count),
            "beta": beta,
            "mu": mu,
        },
    )
