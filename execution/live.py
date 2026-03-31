"""Live execution backend with dry-run safety rails."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from config import Settings
from core.decision import TradeDecision
from core.sizing import SizingResult
from data.markets import KalshiClient, MarketQuote, kalshi_credentials_present
from db.models import Database


@dataclass(frozen=True)
class LiveExecutionResult:
    """Outcome of a live execution attempt."""

    submitted: bool
    order_id: Optional[int]
    remote_order_id: Optional[str]
    message: str


class LiveBroker:
    """Guarded live trading interface for Kalshi."""

    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database

    def place_entry_order(
        self,
        *,
        decision: TradeDecision,
        sizing: SizingResult,
        market: MarketQuote,
    ) -> LiveExecutionResult:
        """Submit a live order or simulate it when dry-run is enabled."""

        if not decision.approved or sizing.contracts < 1:
            return LiveExecutionResult(False, None, None, "decision not approved")
        if not kalshi_credentials_present(self.settings):
            return LiveExecutionResult(False, None, None, "Kalshi credentials missing")

        client_order_id = f"kalshi-weather-{uuid.uuid4()}"
        order_id = self.database.record_order(
            mode="live",
            ticker=market.ticker,
            city_key=market.city_key,
            target_date=market.target_date.isoformat(),
            side=decision.side,
            action="buy",
            contracts=sizing.contracts,
            requested_price=decision.price,
            fill_price=None,
            status="dry-run" if self.settings.dry_run else "submitted",
            order_type="live-entry",
            reason="; ".join(decision.rationale),
            metadata={"client_order_id": client_order_id},
        )
        if self.settings.dry_run:
            return LiveExecutionResult(True, order_id, None, "dry-run live order only recorded locally")

        client = KalshiClient(self.settings)
        response = client.create_order(
            ticker=market.ticker,
            side=decision.side,
            action="buy",
            contracts=sizing.contracts,
            price=decision.price,
            client_order_id=client_order_id,
        )
        remote_order_id = response.get("order", {}).get("order_id")
        return LiveExecutionResult(True, order_id, remote_order_id, "live order submitted")
