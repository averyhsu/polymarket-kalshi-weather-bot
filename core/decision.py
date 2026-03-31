"""Trade decision engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from config import Settings
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
    expected_value: float
    yes_ev: float
    no_ev: float
    rationale: List[str] = field(default_factory=list)


def expected_value(probability: float, cost: float, fee: float, slippage: float) -> float:
    """Expected dollar value for a single binary contract."""

    return probability - cost - fee - slippage


def choose_trade(
    *,
    market: MarketQuote,
    probability: ProbabilityResult,
    risk: RiskAssessment,
    settings: Settings,
) -> TradeDecision:
    """Choose YES or NO, apply entry filters, and return the decision."""

    yes_price = market.executable_yes_price
    no_price = market.executable_no_price
    fee = settings.fee_per_contract
    slippage = settings.slippage_per_contract
    yes_ev = expected_value(probability.p_bucket_yes, yes_price, fee, slippage)
    no_ev = expected_value(probability.p_bucket_no, no_price, fee, slippage)

    rationale: List[str] = []
    if not risk.allowed:
        rationale.extend(risk.reasons)
        return TradeDecision(False, "NONE", 0.0, 0.0, 0.0, yes_ev, no_ev, rationale)

    candidates = []
    if not settings.no_only:
        candidates.append(("YES", yes_price, probability.p_bucket_yes, yes_ev))
    candidates.append(("NO", no_price, probability.p_bucket_no, no_ev))

    best_side, best_price, best_prob, best_ev = max(candidates, key=lambda item: item[3])
    price_cents = int(round(best_price * 100))
    if price_cents < settings.min_price_cents or price_cents > settings.max_price_cents:
        rationale.append(f"{best_side} price {price_cents}c outside allowed range")
    if best_ev < risk.dynamic_min_ev:
        rationale.append(f"{best_side} EV {best_ev:.4f} below dynamic minimum {risk.dynamic_min_ev:.4f}")
    if market.spread_cents > settings.max_spread_cents:
        rationale.append(f"spread {market.spread_cents}c exceeds max")
    if best_side == "YES" and settings.no_only:
        rationale.append("NO-only mode enabled")

    approved = not rationale
    if approved:
        rationale.append(
            f"{best_side} selected with EV {best_ev:.4f}, probability {best_prob:.3f}, spread {market.spread_cents}c"
        )
    return TradeDecision(
        approved=approved,
        side=best_side if approved else "NONE",
        price=best_price if approved else 0.0,
        win_probability=best_prob if approved else 0.0,
        expected_value=best_ev if approved else 0.0,
        yes_ev=yes_ev,
        no_ev=no_ev,
        rationale=rationale,
    )
