"""Trade decision engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from config import Settings
from core.calibration import CalibrationAdjustment, CalibrationContext
from core.probability import ProbabilityResult
from core.risk import RiskAssessment
from data.markets import MarketQuote


@dataclass(frozen=True)
class TradeDecision:
    """Structured decision for a market."""

    approved: bool
    side: str
    price: float
    win_probability: float
    raw_win_probability: float
    expected_value: float
    min_required_ev: float
    ranking_score: float
    yes_ev: float
    no_ev: float
    calibration_sample_size: int
    rationale: List[str] = field(default_factory=list)


def expected_value(probability: float, cost: float, fee: float, slippage: float) -> float:
    """Expected dollar value for a single binary contract."""

    return probability - cost - fee - slippage


def _candidate_ranking_score(expected_value: float, risk: RiskAssessment, spread_cents: int, min_required_ev: float) -> float:
    edge_above_threshold = expected_value - min_required_ev
    return edge_above_threshold * max(risk.size_mult, 0.1) * max(risk.source_health_mult, 0.1) - spread_cents * 0.002


def choose_trade(
    *,
    market: MarketQuote,
    probability: ProbabilityResult,
    risk: RiskAssessment,
    settings: Settings,
    calibration: Optional[CalibrationContext] = None,
) -> TradeDecision:
    """Choose YES or NO, apply entry filters, and return the decision."""

    yes_price = market.executable_yes_price
    no_price = market.executable_no_price
    fee = settings.fee_per_contract
    slippage = settings.slippage_per_contract

    rationale: List[str] = []
    if not risk.allowed:
        rationale.extend(risk.reasons)
        return TradeDecision(False, "NONE", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, rationale)

    candidate_outcomes = []
    for side, price, raw_probability in (
        ("YES", yes_price, probability.p_bucket_yes),
        ("NO", no_price, probability.p_bucket_no),
    ):
        candidate_rationale: List[str] = []
        if side == "YES" and settings.no_only:
            candidate_rationale.append("NO-only mode enabled")
        if side == "YES" and not settings.yes_enabled:
            candidate_rationale.append("YES entries disabled")

        price_cents = int(round(price * 100))
        if price_cents < settings.min_price_cents or price_cents > settings.max_price_cents:
            candidate_rationale.append(f"{side} price {price_cents}c outside allowed range")
        if side == "YES" and price_cents < settings.yes_min_price_cents:
            candidate_rationale.append(f"YES price {price_cents}c below minimum {settings.yes_min_price_cents}c")
        if (
            side == "NO"
            and settings.no_mid_price_filter_enabled
            and settings.no_mid_price_min_cents <= price_cents < settings.no_mid_price_max_cents
        ):
            candidate_rationale.append(
                f"NO price {price_cents}c inside filtered mid-price range "
                f"{settings.no_mid_price_min_cents}c-{settings.no_mid_price_max_cents - 1}c"
            )

        calibration_adjustment = (
            calibration.calibrate(side=side, city_key=market.city_key, price=price, raw_probability=raw_probability)
            if calibration is not None
            else CalibrationAdjustment(calibrated_probability=raw_probability, sample_size=0, regimes=[])
        )
        calibrated_probability = calibration_adjustment.calibrated_probability
        ev = expected_value(calibrated_probability, price, fee, slippage)

        tail_penalty = 0.0
        if (
            side == "YES"
            and price_cents <= settings.tail_yes_price_cents
            and raw_probability >= settings.tail_yes_probability_threshold
            and risk.uncertainty >= settings.tail_uncertainty_threshold
        ):
            tail_penalty = settings.tail_risk_penalty

        side_floor = settings.yes_min_ev if side == "YES" else settings.no_min_ev
        min_required_ev = max(risk.dynamic_min_ev, side_floor) + settings.city_ev_buffer(market.city_key) + tail_penalty
        if ev < min_required_ev:
            candidate_rationale.append(f"{side} EV {ev:.4f} below dynamic minimum {min_required_ev:.4f}")
        if tail_penalty > 0.0:
            candidate_rationale.append(f"{side} tail-risk penalty {tail_penalty:.4f} applied")

        ranking_score = _candidate_ranking_score(ev, risk, market.spread_cents, min_required_ev)
        candidate_outcomes.append(
            {
                "side": side,
                "price": price,
                "raw_probability": raw_probability,
                "probability": calibrated_probability,
                "expected_value": ev,
                "min_required_ev": min_required_ev,
                "ranking_score": ranking_score,
                "sample_size": calibration_adjustment.sample_size,
                "rationale": candidate_rationale,
            }
        )

    yes_candidate = next(item for item in candidate_outcomes if item["side"] == "YES")
    no_candidate = next(item for item in candidate_outcomes if item["side"] == "NO")
    yes_ev = float(yes_candidate["expected_value"])
    no_ev = float(no_candidate["expected_value"])

    approved_candidates = [item for item in candidate_outcomes if not item["rationale"]]
    if not approved_candidates:
        best_rejected = max(candidate_outcomes, key=lambda item: item["expected_value"])
        rationale.extend(best_rejected["rationale"])
        return TradeDecision(False, "NONE", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, yes_ev, no_ev, 0, rationale)

    best = max(approved_candidates, key=lambda item: item["ranking_score"])
    rationale.append(
        f"{best['side']} selected with EV {best['expected_value']:.4f}, probability {best['probability']:.3f}, spread {market.spread_cents}c"
    )
    return TradeDecision(
        approved=True,
        side=str(best["side"]),
        price=float(best["price"]),
        win_probability=float(best["probability"]),
        raw_win_probability=float(best["raw_probability"]),
        expected_value=float(best["expected_value"]),
        min_required_ev=float(best["min_required_ev"]),
        ranking_score=float(best["ranking_score"]),
        yes_ev=yes_ev,
        no_ev=no_ev,
        calibration_sample_size=int(best["sample_size"]),
        rationale=rationale,
    )
