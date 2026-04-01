"""Empirical probability calibration helpers for entry decisions."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _probability_bin(probability: float) -> str:
    lower = int(_clamp(probability) * 10) * 10
    upper = min(lower + 9, 99)
    return f"p{lower:02d}_{upper:02d}"


def _price_bin(price: float) -> str:
    cents = int(round(price * 100))
    if cents <= 10:
        return "price_00_10"
    if cents <= 25:
        return "price_11_25"
    if cents <= 50:
        return "price_26_50"
    if cents <= 75:
        return "price_51_75"
    return "price_76_100"


@dataclass(frozen=True)
class CalibrationSample:
    """Historical realized sample used for empirical shrinkage."""

    city_key: str
    side: str
    price: float
    predicted_win_probability: float
    settled_win: float


@dataclass(frozen=True)
class CalibrationAdjustment:
    """Calibrated candidate probability and supporting diagnostics."""

    calibrated_probability: float
    sample_size: int
    regimes: List[str]


@dataclass(frozen=True)
class CalibrationStats:
    wins: float = 0.0
    count: int = 0


class CalibrationContext:
    """Hierarchical empirical calibration from historical realized samples."""

    def __init__(self, samples: Iterable[CalibrationSample], pseudo_count: float = 12.0):
        self.pseudo_count = max(float(pseudo_count), 0.0)
        self._stats: Dict[Tuple[str, ...], CalibrationStats] = {}
        aggregate: Dict[Tuple[str, ...], List[float]] = defaultdict(lambda: [0.0, 0.0])
        for sample in samples:
            side = sample.side.upper()
            city = sample.city_key.lower()
            prob_bin = _probability_bin(sample.predicted_win_probability)
            price_bin = _price_bin(sample.price)
            keys = [
                ("global", prob_bin),
                ("city", city, prob_bin),
                ("side", side, prob_bin),
                ("side_price", side, price_bin, prob_bin),
                ("side_city", side, city, prob_bin),
                ("side_city_price", side, city, price_bin, prob_bin),
            ]
            for key in keys:
                aggregate[key][0] += float(sample.settled_win)
                aggregate[key][1] += 1.0
        self._stats = {
            key: CalibrationStats(wins=values[0], count=int(values[1]))
            for key, values in aggregate.items()
        }

    @property
    def sample_count(self) -> int:
        global_counts = [stats.count for key, stats in self._stats.items() if key and key[0] == "global"]
        return max(global_counts, default=0)

    def calibrate(self, *, side: str, city_key: str, price: float, raw_probability: float) -> CalibrationAdjustment:
        probability = _clamp(raw_probability)
        side_upper = side.upper()
        city = city_key.lower()
        prob_bin = _probability_bin(probability)
        price_bin = _price_bin(price)
        regimes = [
            "global",
            f"city={city}",
            f"side={side_upper}",
            f"side_price={side_upper}:{price_bin}",
            f"side_city={side_upper}:{city}",
            f"side_city_price={side_upper}:{city}:{price_bin}",
        ]
        lookup_keys = [
            ("global", prob_bin),
            ("city", city, prob_bin),
            ("side", side_upper, prob_bin),
            ("side_price", side_upper, price_bin, prob_bin),
            ("side_city", side_upper, city, prob_bin),
            ("side_city_price", side_upper, city, price_bin, prob_bin),
        ]

        adjusted = probability
        total_samples = 0
        for key in lookup_keys:
            stats = self._stats.get(key)
            if not stats or stats.count <= 0:
                continue
            total_samples += stats.count
            empirical = stats.wins / stats.count
            adjusted = _clamp(
                (adjusted * self.pseudo_count + empirical * stats.count) / (self.pseudo_count + stats.count)
            )
        return CalibrationAdjustment(
            calibrated_probability=adjusted,
            sample_size=total_samples,
            regimes=regimes,
        )


def build_samples_from_backtest_trades(trades: Iterable[Dict[str, object]]) -> List[CalibrationSample]:
    """Convert settled trade dictionaries into calibration samples."""

    samples: List[CalibrationSample] = []
    for trade in trades:
        side = str(trade["side"]).upper()
        predicted_yes = _clamp(float(trade["predicted_probability_yes"]))
        predicted_win = predicted_yes if side == "YES" else 1.0 - predicted_yes
        settled_yes = _clamp(float(trade["settled_yes"]))
        settled_win = settled_yes if side == "YES" else 1.0 - settled_yes
        samples.append(
            CalibrationSample(
                city_key=str(trade["city"]).lower(),
                side=side,
                price=float(trade["entry_price"]),
                predicted_win_probability=predicted_win,
                settled_win=settled_win,
            )
        )
    return samples


def build_samples_from_calibration_rows(rows: Iterable[object]) -> List[CalibrationSample]:
    """Convert persisted calibration rows into calibration samples when metadata is available."""

    samples: List[CalibrationSample] = []
    for row in rows:
        settled_value = row["settled_value"]
        if settled_value is None:
            continue
        side = str(row["side"]).upper()
        if side not in {"YES", "NO"}:
            continue
        metadata_raw = row["metadata"]
        metadata = metadata_raw if isinstance(metadata_raw, dict) else json.loads(str(metadata_raw or "{}"))
        entry_price = metadata.get("entry_price")
        if entry_price is None:
            continue
        predicted_yes = _clamp(float(row["predicted_probability"]))
        predicted_win = predicted_yes if side == "YES" else 1.0 - predicted_yes
        settled_yes = _clamp(float(settled_value))
        settled_win = settled_yes if side == "YES" else 1.0 - settled_yes
        samples.append(
            CalibrationSample(
                city_key=str(row["city_key"]).lower(),
                side=side,
                price=float(entry_price),
                predicted_win_probability=predicted_win,
                settled_win=settled_win,
            )
        )
    return samples
