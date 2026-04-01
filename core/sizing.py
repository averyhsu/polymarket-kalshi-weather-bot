"""Kelly sizing helpers."""

from __future__ import annotations

from dataclasses import dataclass

from config import Settings


@dataclass(frozen=True)
class SizingResult:
    """Sizing output for a candidate trade."""

    raw_kelly: float
    adjusted_kelly: float
    target_size_usd: float
    capped_size_usd: float
    contracts: int
    max_position_cap_usd: float
    cost_per_contract: float
    debug: dict


def calculate_kelly_size(
    *,
    balance: float,
    probability: float,
    cost: float,
    fee: float,
    uncertainty_mult: float,
    source_health_mult: float,
    settings: Settings,
    side: str = "NO",
) -> SizingResult:
    """Calculate fee-aware fractional Kelly sizing."""

    denominator = cost + fee
    if denominator <= 0.0 or cost <= 0.0 or cost >= 1.0:
        return SizingResult(0.0, 0.0, 0.0, 0.0, 0, 0.0, denominator, {"reason": "invalid_cost"})

    b = ((1.0 - cost) - fee) / denominator
    if b <= 0:
        return SizingResult(0.0, 0.0, 0.0, 0.0, 0, 0.0, denominator, {"reason": "invalid_odds", "b": b})

    raw_kelly = max(0.0, (probability * (b + 1.0) - 1.0) / b)

    profile = settings.active_profile
    base_fraction = settings.kelly_fraction * profile.kelly_fraction_mult
    if side.upper() == "YES":
        base_fraction *= settings.yes_kelly_fraction_mult
    if balance <= settings.survival_balance_floor:
        base_fraction = settings.survival_kelly_fraction
    adjusted_kelly = max(0.0, raw_kelly * profile.dampening)

    target_size = balance * adjusted_kelly * base_fraction * uncertainty_mult * source_health_mult
    max_position_cap = min(
        balance * settings.max_position_pct * profile.max_position_mult,
        settings.max_position_usd * profile.max_position_mult,
    )
    capped_size = max(0.0, min(target_size, max_position_cap))
    contracts = int(capped_size // denominator)
    if contracts < 1:
        capped_size = 0.0

    return SizingResult(
        raw_kelly=raw_kelly,
        adjusted_kelly=adjusted_kelly,
        target_size_usd=target_size,
        capped_size_usd=capped_size,
        contracts=max(0, contracts),
        max_position_cap_usd=max_position_cap,
        cost_per_contract=denominator,
        debug={
            "b": b,
            "base_fraction": base_fraction,
            "uncertainty_mult": uncertainty_mult,
            "source_health_mult": source_health_mult,
            "side": side.upper(),
        },
    )
