"""Replay harness using stored forecasts and market snapshots."""

from __future__ import annotations

import json
from datetime import date
from typing import Dict, List

from config import Settings
from core.decision import choose_trade
from core.probability import estimate_bucket_probability
from core.risk import assess_entry_risk
from data.climatology import bucket_probability_from_climatology
from data.markets import MarketQuote
from data.weather import SourceHealth
from db.models import Database


def replay_from_database(settings: Settings, database: Database) -> Dict[str, object]:
    """Re-run entry logic on stored forecasts and latest snapshots."""

    forecasts = database.recent_forecasts()
    latest_by_city_date = {(row["city_key"], row["target_date"]): row for row in forecasts}
    trades: List[Dict[str, object]] = []

    for snapshot in database.latest_snapshots():
        key = (snapshot["city_key"], snapshot["target_date"])
        forecast = latest_by_city_date.get(key)
        if forecast is None:
            continue
        metadata = json.loads(snapshot["metadata"])
        source_health = SourceHealth(
            score=float(metadata.get("source_health_score", 1.0)),
            status=str(metadata.get("source_health_status", "HEALTHY")),
            success=1.0,
            freshness=1.0,
            completeness=1.0,
            consistency=1.0,
        )
        market = MarketQuote(
            ticker=snapshot["ticker"],
            series_ticker=str(metadata.get("series_ticker", "")),
            city_key=snapshot["city_key"],
            city_name=str(metadata.get("city_name", snapshot["city_key"])),
            target_date=date.fromisoformat(snapshot["target_date"]),
            bucket_low=float(metadata.get("bucket_low", float("-inf"))),
            bucket_high=float(metadata.get("bucket_high", float("inf"))),
            strike_label=str(metadata.get("strike_label", "")),
            title=str(metadata.get("title", snapshot["ticker"])),
            subtitle=str(metadata.get("subtitle", "")),
            yes_bid=float(snapshot["yes_bid"]),
            yes_ask=float(snapshot["yes_ask"]),
            no_bid=float(snapshot["no_bid"]),
            no_ask=float(snapshot["no_ask"]),
            last_price=float(metadata.get("last_price", 0.5)),
            volume=float(snapshot["volume"]),
            open_interest=float(metadata.get("open_interest", 0.0)),
            updated_time=None,
            status="open",
        )
        p_climo = bucket_probability_from_climatology(
            market.city_key,
            market.target_date,
            market.bucket_low,
            market.bucket_high,
        )
        probability = estimate_bucket_probability(
            mean_temp_f=float(forecast["mean_temp"]),
            sigma_temp_f=float(forecast["sigma_temp"]),
            bucket_low=market.bucket_low,
            bucket_high=market.bucket_high,
            p_climo=p_climo,
            pseudo_count=settings.pseudo_count,
            boundary_buffer_f=settings.boundary_buffer_f,
            min_sigma_f=settings.min_sigma_f,
        )
        risk = assess_entry_risk(
            settings=settings,
            boundary_mass=probability.boundary_mass,
            disagreement=max(probability.disagreement, float(forecast["disagreement"])),
            spread_cents=int(snapshot["spread_cents"]),
            source_health=source_health,
            open_positions=0,
            realized_pnl_today=0.0,
            bankroll_reference=settings.initial_balance,
        )
        decision = choose_trade(market=market, probability=probability, risk=risk, settings=settings)
        trades.append(
            {
                "ticker": market.ticker,
                "approved": decision.approved,
                "side": decision.side,
                "ev": decision.expected_value,
            }
        )

    approved = [trade for trade in trades if trade["approved"]]
    avg_ev = sum(float(trade["ev"]) for trade in approved) / len(approved) if approved else 0.0
    return {
        "snapshots_replayed": len(trades),
        "approved_trades": len(approved),
        "average_ev": avg_ev,
        "trades": trades[:25],
    }
