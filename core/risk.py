"""Risk and uncertainty scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List

from config import Settings
from data.weather import SourceHealth


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


@dataclass(frozen=True)
class RiskAssessment:
    """Combined uncertainty and entry gating output."""

    uncertainty: float
    dynamic_min_ev: float
    size_mult: float
    source_health_mult: float
    source_health_status: str
    allowed: bool
    reasons: List[str] = field(default_factory=list)


def uncertainty_score(boundary_mass: float, disagreement: float, settings: Settings) -> float:
    """Score model uncertainty from boundary mass and forecast disagreement."""

    boundary_component = _clamp(boundary_mass / max(settings.boundary_threshold, 1e-6))
    disagreement_component = _clamp(disagreement / max(settings.disagreement_threshold, 1e-6))
    return _clamp((0.60 * boundary_component + 0.40 * disagreement_component) / 1.0)


def source_health_multiplier(source_health: SourceHealth) -> float:
    """Map source-health state into a size multiplier."""

    if source_health.status == "HEALTHY":
        return 1.0
    if source_health.status == "DEGRADED":
        return 0.65
    return 0.0


def assess_entry_risk(
    *,
    settings: Settings,
    boundary_mass: float,
    disagreement: float,
    spread_cents: int,
    source_health: SourceHealth,
    open_positions: int,
    realized_pnl_today: float,
    bankroll_reference: float,
) -> RiskAssessment:
    """Assess whether a new trade may be opened."""

    uncertainty = uncertainty_score(boundary_mass, disagreement, settings)
    profile = settings.active_profile
    dynamic_min_ev = settings.base_min_ev * profile.min_ev_buffer_mult + uncertainty * 0.02
    size_mult = max(0.35, 1.0 - 0.60 * uncertainty)
    health_mult = source_health_multiplier(source_health)
    reasons: List[str] = []

    if spread_cents > settings.max_spread_cents:
        reasons.append(f"spread {spread_cents}c exceeds max {settings.max_spread_cents}c")
    if source_health.score < settings.source_health_trade_threshold:
        reasons.append(f"source health {source_health.score:.2f} is below threshold")
    if source_health.status == "BROKEN":
        reasons.append("source health kill switch triggered")
    if open_positions >= settings.max_open_positions:
        reasons.append("max open positions reached")
    if realized_pnl_today <= -abs(bankroll_reference) * settings.daily_max_loss_pct:
        reasons.append("daily max loss circuit breaker triggered")

    return RiskAssessment(
        uncertainty=uncertainty,
        dynamic_min_ev=dynamic_min_ev,
        size_mult=size_mult,
        source_health_mult=health_mult,
        source_health_status=source_health.status,
        allowed=not reasons,
        reasons=reasons,
    )
