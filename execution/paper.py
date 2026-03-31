"""Paper trading execution backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from config import Settings
from core.decision import TradeDecision
from core.sizing import SizingResult
from data.markets import MarketQuote
from db.models import Database, PositionRecord


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of an execution attempt."""

    executed: bool
    order_id: Optional[int]
    position_id: Optional[int]
    fill_price: float
    contracts: int
    message: str


class PaperBroker:
    """Paper execution engine backed by SQLite state."""

    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.database.ensure_paper_account(settings.initial_balance)

    def place_entry_order(
        self,
        *,
        decision: TradeDecision,
        sizing: SizingResult,
        market: MarketQuote,
        probability_yes: float,
    ) -> ExecutionResult:
        """Place a paper entry order using conservative executable prices."""

        if not decision.approved or sizing.contracts < 1:
            return ExecutionResult(False, None, None, 0.0, 0, "decision not approved")
        if self.settings.dry_run:
            return ExecutionResult(False, None, None, decision.price, sizing.contracts, "dry-run entry")

        cash = self.database.get_paper_cash()
        entry_fees = self.settings.fee_per_contract * sizing.contracts
        total_cost = decision.price * sizing.contracts + entry_fees
        if total_cost > cash:
            return ExecutionResult(False, None, None, 0.0, 0, "insufficient paper cash")

        order_id = self.database.record_order(
            mode="paper",
            ticker=market.ticker,
            city_key=market.city_key,
            target_date=market.target_date.isoformat(),
            side=decision.side,
            action="buy",
            contracts=sizing.contracts,
            requested_price=decision.price,
            fill_price=decision.price,
            status="filled",
            order_type="paper-entry",
            reason="; ".join(decision.rationale),
            metadata={
                "expected_value": decision.expected_value,
                "sizing": sizing.debug,
                "bucket_low": market.bucket_low,
                "bucket_high": market.bucket_high,
            },
        )
        self.database.record_fill(
            order_id=order_id,
            ticker=market.ticker,
            side=decision.side,
            action="buy",
            contracts=sizing.contracts,
            price=decision.price,
            fees=entry_fees,
            metadata={"mode": "paper"},
        )
        position_id = self.database.open_position(
            mode="paper",
            ticker=market.ticker,
            city_key=market.city_key,
            target_date=market.target_date.isoformat(),
            side=decision.side,
            contracts=sizing.contracts,
            avg_price=decision.price,
            entry_probability=probability_yes,
            metadata={
                "entry_fees": entry_fees,
                "entry_probability_yes": probability_yes,
                "bucket_low": market.bucket_low,
                "bucket_high": market.bucket_high,
                "strike_label": market.strike_label,
                "city_name": market.city_name,
            },
        )
        self.database.set_paper_cash(cash - total_cost)
        return ExecutionResult(True, order_id, position_id, decision.price, sizing.contracts, "paper order filled")

    def mark_position(self, position: PositionRecord, market: MarketQuote, current_probability_yes: float) -> None:
        """Mark an open position to the current executable bid."""

        current_mark = market.yes_bid if position.side == "YES" else market.no_bid
        entry_fees = float(position.metadata.get("entry_fees", 0.0))
        unrealized = (current_mark - position.avg_price) * position.contracts - entry_fees
        self.database.update_position_mark(position.id, current_mark, current_probability_yes, unrealized)

    def exit_position(
        self,
        *,
        position: PositionRecord,
        market: MarketQuote,
        reason: str,
        current_probability_yes: float,
    ) -> ExecutionResult:
        """Close a paper position at the current bid."""

        exit_price = market.yes_bid if position.side == "YES" else market.no_bid
        if exit_price <= 0.0:
            return ExecutionResult(False, None, position.id, 0.0, 0, "no executable bid available")
        if self.settings.dry_run:
            return ExecutionResult(False, None, position.id, exit_price, position.contracts, f"dry-run exit: {reason}")

        exit_fees = self.settings.fee_per_contract * position.contracts
        entry_fees = float(position.metadata.get("entry_fees", 0.0))
        cash = self.database.get_paper_cash()
        self.database.set_paper_cash(cash + exit_price * position.contracts - exit_fees)

        order_id = self.database.record_order(
            mode="paper",
            ticker=position.ticker,
            city_key=position.city_key,
            target_date=position.target_date,
            side=position.side,
            action="sell",
            contracts=position.contracts,
            requested_price=exit_price,
            fill_price=exit_price,
            status="filled",
            order_type="paper-exit",
            reason=reason,
            metadata={"current_probability_yes": current_probability_yes},
        )
        self.database.record_fill(
            order_id=order_id,
            ticker=position.ticker,
            side=position.side,
            action="sell",
            contracts=position.contracts,
            price=exit_price,
            fees=exit_fees,
            metadata={"mode": "paper", "reason": reason},
        )
        realized = (exit_price - position.avg_price) * position.contracts - entry_fees - exit_fees
        self.database.close_position(
            position.id,
            current_mark=exit_price,
            realized_pnl=realized,
            metadata={"close_reason": reason, "current_probability_yes": current_probability_yes},
        )
        return ExecutionResult(True, order_id, position.id, exit_price, position.contracts, f"paper exit filled: {reason}")

    def settle_position(self, position: PositionRecord, observed_high: float) -> ExecutionResult:
        """Settle a matured paper position against the observed high."""

        metadata = dict(position.metadata)
        bucket_low = float(metadata.get("bucket_low", float("-inf")))
        bucket_high = float(metadata.get("bucket_high", float("inf")))
        yes_settles = 1.0 if bucket_low <= observed_high < bucket_high else 0.0
        payout = yes_settles if position.side == "YES" else 1.0 - yes_settles
        if self.settings.dry_run:
            return ExecutionResult(False, None, position.id, payout, position.contracts, "dry-run settlement")

        entry_fees = float(metadata.get("entry_fees", 0.0))
        cash = self.database.get_paper_cash()
        self.database.set_paper_cash(cash + payout * position.contracts)
        realized = payout * position.contracts - position.avg_price * position.contracts - entry_fees
        self.database.close_position(
            position.id,
            current_mark=payout,
            realized_pnl=realized,
            metadata={"settlement_high": observed_high, "close_reason": "settlement"},
        )
        self.database.record_calibration(
            ticker=position.ticker,
            city_key=position.city_key,
            target_date=position.target_date,
            side=position.side,
            predicted_probability=float(metadata.get("entry_probability_yes", 0.5)),
            settled_value=yes_settles,
            bucket_low=bucket_low,
            bucket_high=bucket_high,
            metadata={"settlement_high": observed_high},
        )
        return ExecutionResult(True, None, position.id, payout, position.contracts, "paper position settled")

    def summary(self) -> Dict[str, float]:
        """Return paper account balances and P&L."""

        cash = self.database.get_paper_cash()
        positions = self.database.fetch_all_positions(mode="paper")
        open_positions = [position for position in positions if position.status == "open"]
        closed_positions = [position for position in positions if position.status == "closed"]
        open_market_value = sum(position.current_mark * position.contracts for position in open_positions)
        realized = sum(position.realized_pnl for position in closed_positions)
        unrealized = sum(position.unrealized_pnl for position in open_positions)
        starting = float(self.database.get_setting("paper_starting_balance") or 0.0)
        equity = cash + open_market_value
        return {
            "cash": cash,
            "starting_balance": starting,
            "equity": equity,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
        }
