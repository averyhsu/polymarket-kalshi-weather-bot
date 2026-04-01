"""Helpers for ranking and selecting entry candidates under portfolio caps."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Dict, List, Tuple


def _target_date_label(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def select_ranked_candidates(
    candidates: List[dict],
    *,
    remaining_slots: int,
    max_positions_per_city_day: int,
    existing_city_day_counts: Dict[Tuple[str, str], int] | None = None,
) -> Tuple[List[dict], List[Tuple[dict, str]]]:
    """Select the highest-ranked candidates while respecting exposure limits."""

    selected: List[dict] = []
    rejected: List[Tuple[dict, str]] = []
    city_day_counts = defaultdict(int)
    for (city_key, target_date), count in (existing_city_day_counts or {}).items():
        city_day_counts[(str(city_key).lower(), _target_date_label(target_date))] = int(count)

    ranked = sorted(
        candidates,
        key=lambda item: (
            float(item["decision"].ranking_score),
            float(item["decision"].expected_value),
            -int(getattr(item.get("quote") or item["market"], "spread_cents", 0)),
        ),
        reverse=True,
    )
    for candidate in ranked:
        key = (str(candidate["city_key"]).lower(), _target_date_label(candidate["target_date"]))
        if len(selected) >= max(remaining_slots, 0):
            rejected.append((candidate, "higher ranked candidates filled available slots"))
            continue
        if city_day_counts[key] >= max_positions_per_city_day:
            rejected.append((candidate, "city/date exposure cap reached"))
            continue
        selected.append(candidate)
        city_day_counts[key] += 1
    return selected, rejected
