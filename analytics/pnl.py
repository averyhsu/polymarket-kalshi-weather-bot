"""P&L analytics helpers."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Optional

from db.models import Database


def summarize_pnl(database: Database, mode: Optional[str] = "paper") -> Dict[str, object]:
    """Build a P&L and win-rate summary from persisted state."""

    positions = database.fetch_all_positions(mode=mode)
    open_positions = [position for position in positions if position.status == "open"]
    closed_positions = [position for position in positions if position.status == "closed"]
    cash = database.get_paper_cash()
    starting_balance = float(database.get_setting("paper_starting_balance") or 0.0)
    open_market_value = sum(position.current_mark * position.contracts for position in open_positions)
    realized = sum(position.realized_pnl for position in closed_positions)
    unrealized = sum(position.unrealized_pnl for position in open_positions)
    equity = cash + open_market_value

    by_city = defaultdict(lambda: {"trades": 0, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "wins": 0})
    by_side = defaultdict(lambda: {"trades": 0, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "wins": 0})
    wins = 0
    for position in positions:
        by_city[position.city_key]["trades"] += 1
        by_city[position.city_key]["realized_pnl"] += position.realized_pnl
        by_city[position.city_key]["unrealized_pnl"] += position.unrealized_pnl
        by_side[position.side]["trades"] += 1
        by_side[position.side]["realized_pnl"] += position.realized_pnl
        by_side[position.side]["unrealized_pnl"] += position.unrealized_pnl

    for position in closed_positions:
        if position.realized_pnl > 0:
            wins += 1
        by_city[position.city_key]["wins"] += int(position.realized_pnl > 0)
        by_side[position.side]["wins"] += int(position.realized_pnl > 0)

    total_closed = len(closed_positions)
    return {
        "starting_balance": starting_balance,
        "cash": cash,
        "equity": equity,
        "total_pnl": equity - starting_balance,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_trades": len(positions),
        "closed_trades": total_closed,
        "open_trades": len(open_positions),
        "win_rate": (wins / total_closed) if total_closed else 0.0,
        "by_city": dict(by_city),
        "by_side": dict(by_side),
    }
