"""Lightweight climatology priors for daily high temperature buckets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import erf, sqrt
from typing import Dict, Optional


@dataclass(frozen=True)
class ClimatologyStats:
    """Seed climatology statistics for a city and approximate day-of-year bucket."""

    mean_high_f: float
    sigma_f: float


# Simple month-level seed climatology. This is intentionally lightweight and can
# be replaced with station-specific historical normals later.
CLIMO_SEEDS: Dict[str, Dict[int, ClimatologyStats]] = {
    "nyc": {
        1: ClimatologyStats(39.0, 7.0),
        2: ClimatologyStats(42.0, 7.0),
        3: ClimatologyStats(50.0, 8.0),
        4: ClimatologyStats(61.0, 8.0),
        5: ClimatologyStats(71.0, 7.0),
        6: ClimatologyStats(80.0, 6.0),
        7: ClimatologyStats(85.0, 5.0),
        8: ClimatologyStats(83.0, 5.0),
        9: ClimatologyStats(76.0, 6.0),
        10: ClimatologyStats(64.0, 7.0),
        11: ClimatologyStats(54.0, 7.0),
        12: ClimatologyStats(44.0, 7.0),
    },
    "chicago": {
        1: ClimatologyStats(31.0, 8.0),
        2: ClimatologyStats(35.0, 8.0),
        3: ClimatologyStats(46.0, 9.0),
        4: ClimatologyStats(58.0, 9.0),
        5: ClimatologyStats(69.0, 8.0),
        6: ClimatologyStats(79.0, 7.0),
        7: ClimatologyStats(84.0, 6.0),
        8: ClimatologyStats(82.0, 6.0),
        9: ClimatologyStats(75.0, 7.0),
        10: ClimatologyStats(62.0, 8.0),
        11: ClimatologyStats(48.0, 8.0),
        12: ClimatologyStats(36.0, 8.0),
    },
    "miami": {
        1: ClimatologyStats(76.0, 4.0),
        2: ClimatologyStats(77.0, 4.0),
        3: ClimatologyStats(80.0, 4.0),
        4: ClimatologyStats(83.0, 4.0),
        5: ClimatologyStats(86.0, 4.0),
        6: ClimatologyStats(89.0, 3.5),
        7: ClimatologyStats(91.0, 3.0),
        8: ClimatologyStats(91.0, 3.0),
        9: ClimatologyStats(89.0, 3.5),
        10: ClimatologyStats(86.0, 4.0),
        11: ClimatologyStats(81.0, 4.0),
        12: ClimatologyStats(78.0, 4.0),
    },
    "los_angeles": {
        1: ClimatologyStats(67.0, 5.0),
        2: ClimatologyStats(68.0, 5.0),
        3: ClimatologyStats(69.0, 5.0),
        4: ClimatologyStats(72.0, 5.0),
        5: ClimatologyStats(73.0, 5.0),
        6: ClimatologyStats(77.0, 4.5),
        7: ClimatologyStats(83.0, 4.5),
        8: ClimatologyStats(85.0, 4.5),
        9: ClimatologyStats(84.0, 4.5),
        10: ClimatologyStats(79.0, 5.0),
        11: ClimatologyStats(73.0, 5.0),
        12: ClimatologyStats(67.0, 5.0),
    },
    "denver": {
        1: ClimatologyStats(46.0, 8.0),
        2: ClimatologyStats(47.0, 8.0),
        3: ClimatologyStats(56.0, 9.0),
        4: ClimatologyStats(62.0, 10.0),
        5: ClimatologyStats(71.0, 9.0),
        6: ClimatologyStats(83.0, 8.0),
        7: ClimatologyStats(89.0, 7.0),
        8: ClimatologyStats(86.0, 7.0),
        9: ClimatologyStats(79.0, 8.0),
        10: ClimatologyStats(66.0, 8.0),
        11: ClimatologyStats(53.0, 8.0),
        12: ClimatologyStats(45.0, 8.0),
    },
}


def _normal_cdf(x: float, mean: float, sigma: float) -> float:
    z = (x - mean) / (sigma * sqrt(2.0))
    return 0.5 * (1.0 + erf(z))


def lookup_city_climatology(city_key: str, target_date: date) -> Optional[ClimatologyStats]:
    """Return month-level climatology for the requested city."""

    month_map = CLIMO_SEEDS.get(city_key.lower())
    if not month_map:
        return None
    return month_map.get(target_date.month)


def bucket_probability_from_climatology(city_key: str, target_date: date, bucket_low: float, bucket_high: float) -> float:
    """Approximate bucket probability using a Gaussian climatology prior."""

    stats = lookup_city_climatology(city_key, target_date)
    if stats is None:
        return 0.5
    sigma = max(stats.sigma_f, 1.0)
    lower = 0.0 if bucket_low == float("-inf") else _normal_cdf(bucket_low, stats.mean_high_f, sigma)
    upper = 1.0 if bucket_high == float("inf") else _normal_cdf(bucket_high, stats.mean_high_f, sigma)
    probability = max(0.0, min(1.0, upper - lower))
    if probability <= 0.0:
        return 0.01
    if probability >= 1.0:
        return 0.99
    return probability
