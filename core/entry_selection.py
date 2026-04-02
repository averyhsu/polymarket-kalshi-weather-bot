"""Helpers for ranking and selecting entry candidates under portfolio caps."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from itertools import combinations
from typing import Dict, List, Tuple


def _target_date_label(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _candidate_instrument(candidate: dict):
    return candidate.get("quote") or candidate.get("market")


def _supports_event_basket(candidate: dict) -> bool:
    instrument = _candidate_instrument(candidate)
    decision = candidate.get("decision")
    sizing = candidate.get("sizing")
    probability = candidate.get("probability")
    return all(
        [
            instrument is not None,
            hasattr(instrument, "bucket_low"),
            hasattr(instrument, "bucket_high"),
            decision is not None,
            hasattr(decision, "side"),
            hasattr(decision, "price"),
            hasattr(decision, "ranking_score"),
            sizing is not None,
            hasattr(sizing, "contracts"),
            probability is not None,
            hasattr(probability, "p_bucket_yes"),
        ]
    )


def _legacy_select_ranked_candidates(
    candidates: List[dict],
    *,
    remaining_slots: int,
    max_positions_per_city_day: int,
    existing_city_day_counts: Dict[Tuple[str, str], int] | None = None,
) -> Tuple[List[dict], List[Tuple[dict, str]]]:
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
            -int(getattr(_candidate_instrument(item), "spread_cents", 0)),
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


def _bundle_score(
    subset: Tuple[dict, ...],
    *,
    fee_per_contract: float,
    slippage_per_contract: float,
    downside_penalty: float,
) -> float:
    total_cost = 0.0
    states: List[Tuple[float, dict | None]] = []
    bucket_probability_sum = 0.0
    for candidate in subset:
        decision = candidate["decision"]
        sizing = candidate["sizing"]
        probability = candidate["probability"]
        contracts = int(getattr(sizing, "contracts", 0))
        per_contract_cost = float(decision.price) + fee_per_contract + slippage_per_contract
        total_cost += contracts * per_contract_cost
        bucket_probability = max(0.0, min(1.0, float(probability.p_bucket_yes)))
        bucket_probability_sum += bucket_probability
        states.append((bucket_probability, candidate))

    residual_probability = max(0.0, 1.0 - bucket_probability_sum)
    states.append((residual_probability, None))

    expected_pnl = 0.0
    worst_case_pnl = 0.0
    initialized = False
    for state_probability, winning_candidate in states:
        gross_payout = 0.0
        for candidate in subset:
            decision = candidate["decision"]
            contracts = int(candidate["sizing"].contracts)
            if decision.side == "YES":
                gross_payout += float(contracts if candidate is winning_candidate else 0)
            else:
                gross_payout += float(0 if candidate is winning_candidate else contracts)
        state_pnl = gross_payout - total_cost
        expected_pnl += state_probability * state_pnl
        if not initialized or state_pnl < worst_case_pnl:
            worst_case_pnl = state_pnl
            initialized = True

    return expected_pnl - downside_penalty * max(0.0, -worst_case_pnl)


def select_ranked_candidates(
    candidates: List[dict],
    *,
    remaining_slots: int,
    max_positions_per_city_day: int,
    existing_city_day_counts: Dict[Tuple[str, str], int] | None = None,
    fee_per_contract: float = 0.01,
    slippage_per_contract: float = 0.0,
    max_yes_positions_per_city_day: int = 1,
    allow_mixed_sides_per_city_day: bool = False,
    event_worst_case_penalty: float = 0.35,
) -> Tuple[List[dict], List[Tuple[dict, str]]]:
    """Select candidate baskets by city/day rather than individual scan order."""

    if not candidates:
        return [], []
    if not all(_supports_event_basket(candidate) for candidate in candidates):
        return _legacy_select_ranked_candidates(
            candidates,
            remaining_slots=remaining_slots,
            max_positions_per_city_day=max_positions_per_city_day,
            existing_city_day_counts=existing_city_day_counts,
        )

    normalized_existing_counts = defaultdict(int)
    for (city_key, target_date), count in (existing_city_day_counts or {}).items():
        normalized_existing_counts[(str(city_key).lower(), _target_date_label(target_date))] = int(count)

    grouped: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for candidate in candidates:
        key = (str(candidate["city_key"]).lower(), _target_date_label(candidate["target_date"]))
        grouped[key].append(candidate)

    event_options: Dict[Tuple[str, str], List[dict]] = {}
    event_best_score: Dict[Tuple[str, str], float] = {}
    city_cap_reached: set[Tuple[str, str]] = set()
    for event_key, event_candidates in grouped.items():
        event_capacity = max(0, max_positions_per_city_day - normalized_existing_counts[event_key])
        if event_capacity <= 0:
            city_cap_reached.add(event_key)
            event_options[event_key] = []
            event_best_score[event_key] = float("-inf")
            continue

        ranked_event_candidates = sorted(
            event_candidates,
            key=lambda item: (
                float(item["decision"].ranking_score),
                float(item["decision"].expected_value),
                -int(getattr(_candidate_instrument(item), "spread_cents", 0)),
            ),
            reverse=True,
        )
        options: List[dict] = []
        max_subset_size = min(event_capacity, len(ranked_event_candidates))
        for subset_size in range(1, max_subset_size + 1):
            for subset in combinations(ranked_event_candidates, subset_size):
                sides = {candidate["decision"].side for candidate in subset}
                yes_count = sum(1 for candidate in subset if candidate["decision"].side == "YES")
                if yes_count > max_yes_positions_per_city_day:
                    continue
                if not allow_mixed_sides_per_city_day and len(sides) > 1:
                    continue
                score = _bundle_score(
                    subset,
                    fee_per_contract=fee_per_contract,
                    slippage_per_contract=slippage_per_contract,
                    downside_penalty=event_worst_case_penalty,
                )
                options.append(
                    {
                        "candidates": list(subset),
                        "score": score,
                        "slot_count": len(subset),
                    }
                )
        options.sort(key=lambda item: (float(item["score"]), -int(item["slot_count"])), reverse=True)
        event_options[event_key] = options
        event_best_score[event_key] = float(options[0]["score"]) if options else float("-inf")

    ordered_events = sorted(grouped.keys())
    dp: Dict[int, Tuple[float, Dict[Tuple[str, str], dict]]] = {0: (0.0, {})}
    for event_key in ordered_events:
        next_dp = dict(dp)
        for used_slots, (score, selections) in dp.items():
            for option in event_options[event_key]:
                if option["score"] <= 0.0:
                    continue
                new_slots = used_slots + int(option["slot_count"])
                if new_slots > max(remaining_slots, 0):
                    continue
                new_score = score + float(option["score"])
                best_existing = next_dp.get(new_slots)
                if best_existing is not None and best_existing[0] >= new_score:
                    continue
                new_selections = dict(selections)
                new_selections[event_key] = option
                next_dp[new_slots] = (new_score, new_selections)
        dp = next_dp

    used_slots, (_, selected_options) = max(
        dp.items(),
        key=lambda item: (item[1][0], -item[0]),
    )
    selected: List[dict] = []
    selected_tickers = set()
    for option in selected_options.values():
        for candidate in option["candidates"]:
            selected.append(candidate)
            selected_tickers.add(candidate["ticker"])

    rejected: List[Tuple[dict, str]] = []
    for event_key, event_candidates in grouped.items():
        chosen_option = selected_options.get(event_key)
        chosen_tickers = set()
        if chosen_option is not None:
            chosen_tickers = {candidate["ticker"] for candidate in chosen_option["candidates"]}
        for candidate in event_candidates:
            if candidate["ticker"] in selected_tickers:
                continue
            if event_key in city_cap_reached:
                reason = "city/date exposure cap reached"
            elif chosen_tickers:
                reason = "event basket optimizer selected a different bundle"
            elif event_best_score[event_key] <= 0.0:
                reason = "event basket optimizer preferred no trade"
            elif used_slots >= max(remaining_slots, 0):
                reason = "higher ranked event baskets filled available slots"
            else:
                reason = "event basket optimizer selected a different bundle"
            rejected.append((candidate, reason))

    selected.sort(
        key=lambda item: (
            float(item["decision"].ranking_score),
            float(item["decision"].expected_value),
            -int(getattr(_candidate_instrument(item), "spread_cents", 0)),
        ),
        reverse=True,
    )
    return selected, rejected
