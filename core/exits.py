"""Exit logic for open positions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

from config import Settings
from data.markets import MarketQuote
from db.models import PositionRecord


@dataclass(frozen=True)
class ExitDecision:
    """Exit decision for an open paper or live position."""

    action: str
    reason: str
    held_probability: float
    current_ev: float
    unrealized_cents: float


def evaluate_exit(
    *,
    position: PositionRecord,
    market: MarketQuote,
    entry_probability_yes: float,
    current_probability_yes: float,
    settings: Settings,
    now: Optional[datetime] = None,
) -> ExitDecision:
    """Evaluate exits with the requested priority order."""

    now = now or datetime.utcnow()
    event_cutoff = datetime.combine(market.target_date, time.min)
    hours_to_event = (event_cutoff - now).total_seconds() / 3600.0

    held_probability = current_probability_yes if position.side == "YES" else 1.0 - current_probability_yes
    entry_held_probability = entry_probability_yes if position.side == "YES" else 1.0 - entry_probability_yes
    current_bid = market.yes_bid if position.side == "YES" else market.no_bid
    current_ev = held_probability - current_bid - settings.fee_per_contract - settings.slippage_per_contract
    unrealized_cents = (current_bid - position.avg_price) * 100.0
    liquidity_ok = market.spread_cents <= settings.max_spread_cents * 2

    if hours_to_event <= settings.closeout_hours_before and current_bid > 0.0:
        return ExitDecision("EXIT", "closeout near event", held_probability, current_ev, unrealized_cents)
    if unrealized_cents <= -float(settings.stop_loss_cents):
        return ExitDecision("EXIT", "stop loss", held_probability, current_ev, unrealized_cents)
    if entry_held_probability - held_probability >= settings.probability_drift_threshold:
        return ExitDecision("EXIT", "probability drift", held_probability, current_ev, unrealized_cents)
    if current_ev <= settings.hold_min_ev and liquidity_ok:
        return ExitDecision("EXIT", "expected value gone", held_probability, current_ev, unrealized_cents)
    if unrealized_cents >= float(settings.take_profit_cents) and liquidity_ok:
        return ExitDecision("EXIT", "profit take", held_probability, current_ev, unrealized_cents)
    return ExitDecision("HOLD", "hold", held_probability, current_ev, unrealized_cents)
