"""SQLite schema and persistence helpers for the Kalshi weather bot."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _utcnow() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _from_json(value: Optional[str], default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


@dataclass
class PositionRecord:
    """Open or closed position stored in SQLite."""

    id: int
    mode: str
    ticker: str
    city_key: str
    target_date: str
    side: str
    contracts: int
    avg_price: float
    status: str
    opened_at: str
    closed_at: Optional[str]
    fill_price: float
    current_mark: float
    unrealized_pnl: float
    realized_pnl: float
    metadata: Dict[str, Any]


class Database:
    """Thin SQLite wrapper with explicit methods for bot state and analytics."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=MEMORY")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA temp_store=MEMORY")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    city_key TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    side TEXT NOT NULL,
                    action TEXT NOT NULL,
                    contracts INTEGER NOT NULL,
                    requested_price REAL NOT NULL,
                    fill_price REAL,
                    status TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    reason TEXT,
                    metadata TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    filled_at TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    side TEXT NOT NULL,
                    action TEXT NOT NULL,
                    contracts INTEGER NOT NULL,
                    price REAL NOT NULL,
                    fees REAL NOT NULL,
                    metadata TEXT NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders(id)
                );

                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode TEXT NOT NULL DEFAULT 'paper',
                    ticker TEXT NOT NULL,
                    city_key TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    side TEXT NOT NULL,
                    contracts INTEGER NOT NULL,
                    avg_price REAL NOT NULL,
                    fill_price REAL NOT NULL,
                    current_mark REAL NOT NULL,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    entry_probability REAL NOT NULL,
                    current_probability REAL,
                    unrealized_pnl REAL NOT NULL DEFAULT 0,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    metadata TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    city_key TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    yes_bid REAL NOT NULL,
                    yes_ask REAL NOT NULL,
                    no_bid REAL NOT NULL,
                    no_ask REAL NOT NULL,
                    spread_cents INTEGER NOT NULL,
                    volume REAL NOT NULL,
                    metadata TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_pnl (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trading_day TEXT NOT NULL UNIQUE,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    unrealized_pnl REAL NOT NULL DEFAULT 0,
                    total_fees REAL NOT NULL DEFAULT 0,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS calibration_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    city_key TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    side TEXT NOT NULL,
                    predicted_probability REAL NOT NULL,
                    settled_value REAL,
                    bucket_low REAL,
                    bucket_high REAL,
                    brier_component REAL,
                    metadata TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    city_key TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    mean_temp REAL NOT NULL,
                    sigma_temp REAL NOT NULL,
                    member_count INTEGER NOT NULL,
                    member_spread REAL NOT NULL,
                    disagreement REAL NOT NULL,
                    source_health REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )
            self._ensure_column(connection, "positions", "mode", "TEXT NOT NULL DEFAULT 'paper'")
            if self.get_setting("paper_cash") is None:
                self.set_setting("paper_cash", "0")
            if self.get_setting("paper_starting_balance") is None:
                self.set_setting("paper_starting_balance", "0")

    def _ensure_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing_columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            connection.commit()

    def get_setting(self, key: str) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return None if row is None else str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            connection.commit()

    def ensure_paper_account(self, initial_balance: float) -> None:
        current = float(self.get_setting("paper_cash") or 0.0)
        starting = float(self.get_setting("paper_starting_balance") or 0.0)
        if current <= 0.0:
            self.set_setting("paper_cash", f"{initial_balance:.4f}")
        if starting <= 0.0:
            self.set_setting("paper_starting_balance", f"{initial_balance:.4f}")

    def get_paper_cash(self) -> float:
        return float(self.get_setting("paper_cash") or 0.0)

    def set_paper_cash(self, value: float) -> None:
        self.set_setting("paper_cash", f"{value:.4f}")

    def record_order(
        self,
        *,
        mode: str,
        ticker: str,
        city_key: str,
        target_date: str,
        side: str,
        action: str,
        contracts: int,
        requested_price: float,
        fill_price: Optional[float],
        status: str,
        order_type: str,
        reason: str,
        metadata: Dict[str, Any],
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO orders(
                    created_at, mode, ticker, city_key, target_date, side, action,
                    contracts, requested_price, fill_price, status, order_type, reason, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utcnow(),
                    mode,
                    ticker,
                    city_key,
                    target_date,
                    side,
                    action,
                    contracts,
                    requested_price,
                    fill_price,
                    status,
                    order_type,
                    reason,
                    _to_json(metadata),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def record_fill(
        self,
        *,
        order_id: int,
        ticker: str,
        side: str,
        action: str,
        contracts: int,
        price: float,
        fees: float,
        metadata: Dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO fills(
                    order_id, filled_at, ticker, side, action, contracts, price, fees, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    _utcnow(),
                    ticker,
                    side,
                    action,
                    contracts,
                    price,
                    fees,
                    _to_json(metadata),
                ),
            )
            connection.commit()

    def open_position(
        self,
        *,
        mode: str = "paper",
        ticker: str,
        city_key: str,
        target_date: str,
        side: str,
        contracts: int,
        avg_price: float,
        entry_probability: float,
        metadata: Dict[str, Any],
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO positions(
                    mode, ticker, city_key, target_date, side, contracts, avg_price, fill_price,
                    current_mark, status, opened_at, entry_probability, current_probability,
                    metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
                """,
                (
                    mode,
                    ticker,
                    city_key,
                    target_date,
                    side,
                    contracts,
                    avg_price,
                    avg_price,
                    avg_price,
                    _utcnow(),
                    entry_probability,
                    entry_probability,
                    _to_json(metadata),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def update_position_mark(self, position_id: int, current_mark: float, current_probability: float, unrealized_pnl: float) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE positions
                SET current_mark = ?, current_probability = ?, unrealized_pnl = ?
                WHERE id = ?
                """,
                (current_mark, current_probability, unrealized_pnl, position_id),
            )
            connection.commit()

    def close_position(self, position_id: int, current_mark: float, realized_pnl: float, metadata: Dict[str, Any]) -> None:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT metadata, unrealized_pnl FROM positions WHERE id = ?",
                (position_id,),
            ).fetchone()
            merged = dict(_from_json(existing["metadata"], {})) if existing else {}
            merged.update(metadata)
            connection.execute(
                """
                UPDATE positions
                SET current_mark = ?, status = 'closed', closed_at = ?, realized_pnl = ?, metadata = ?
                WHERE id = ?
                """,
                (current_mark, _utcnow(), realized_pnl, _to_json(merged), position_id),
            )
            connection.commit()

    def fetch_open_positions(self, mode: Optional[str] = None) -> List[PositionRecord]:
        with self.connect() as connection:
            if mode is None:
                rows = connection.execute(
                    "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM positions WHERE status = 'open' AND mode = ? ORDER BY opened_at ASC",
                    (mode,),
                ).fetchall()
        return [
            PositionRecord(
                id=int(row["id"]),
                mode=str(row["mode"]),
                ticker=str(row["ticker"]),
                city_key=str(row["city_key"]),
                target_date=str(row["target_date"]),
                side=str(row["side"]),
                contracts=int(row["contracts"]),
                avg_price=float(row["avg_price"]),
                status=str(row["status"]),
                opened_at=str(row["opened_at"]),
                closed_at=row["closed_at"],
                fill_price=float(row["fill_price"]),
                current_mark=float(row["current_mark"]),
                unrealized_pnl=float(row["unrealized_pnl"]),
                realized_pnl=float(row["realized_pnl"]),
                metadata=_from_json(row["metadata"], {}),
            )
            for row in rows
        ]

    def fetch_all_positions(self, mode: Optional[str] = None) -> List[PositionRecord]:
        with self.connect() as connection:
            if mode is None:
                rows = connection.execute("SELECT * FROM positions ORDER BY opened_at ASC").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM positions WHERE mode = ? ORDER BY opened_at ASC",
                    (mode,),
                ).fetchall()
        return [
            PositionRecord(
                id=int(row["id"]),
                mode=str(row["mode"]),
                ticker=str(row["ticker"]),
                city_key=str(row["city_key"]),
                target_date=str(row["target_date"]),
                side=str(row["side"]),
                contracts=int(row["contracts"]),
                avg_price=float(row["avg_price"]),
                status=str(row["status"]),
                opened_at=str(row["opened_at"]),
                closed_at=row["closed_at"],
                fill_price=float(row["fill_price"]),
                current_mark=float(row["current_mark"]),
                unrealized_pnl=float(row["unrealized_pnl"]),
                realized_pnl=float(row["realized_pnl"]),
                metadata=_from_json(row["metadata"], {}),
            )
            for row in rows
        ]

    def record_snapshot(
        self,
        *,
        ticker: str,
        city_key: str,
        target_date: str,
        yes_bid: float,
        yes_ask: float,
        no_bid: float,
        no_ask: float,
        spread_cents: int,
        volume: float,
        metadata: Dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO snapshots(
                    captured_at, ticker, city_key, target_date, yes_bid, yes_ask, no_bid,
                    no_ask, spread_cents, volume, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utcnow(),
                    ticker,
                    city_key,
                    target_date,
                    yes_bid,
                    yes_ask,
                    no_bid,
                    no_ask,
                    spread_cents,
                    volume,
                    _to_json(metadata),
                ),
            )
            connection.commit()

    def record_forecast(
        self,
        *,
        city_key: str,
        target_date: str,
        provider: str,
        mean_temp: float,
        sigma_temp: float,
        member_count: int,
        member_spread: float,
        disagreement: float,
        source_health: float,
        payload: Dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO forecasts(
                    created_at, city_key, target_date, provider, mean_temp, sigma_temp,
                    member_count, member_spread, disagreement, source_health, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utcnow(),
                    city_key,
                    target_date,
                    provider,
                    mean_temp,
                    sigma_temp,
                    member_count,
                    member_spread,
                    disagreement,
                    source_health,
                    _to_json(payload),
                ),
            )
            connection.commit()

    def record_calibration(
        self,
        *,
        ticker: str,
        city_key: str,
        target_date: str,
        side: str,
        predicted_probability: float,
        settled_value: Optional[float],
        bucket_low: Optional[float],
        bucket_high: Optional[float],
        metadata: Dict[str, Any],
    ) -> None:
        brier_component = None
        if settled_value is not None:
            brier_component = (predicted_probability - settled_value) ** 2
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO calibration_logs(
                    created_at, ticker, city_key, target_date, side, predicted_probability,
                    settled_value, bucket_low, bucket_high, brier_component, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utcnow(),
                    ticker,
                    city_key,
                    target_date,
                    side,
                    predicted_probability,
                    settled_value,
                    bucket_low,
                    bucket_high,
                    brier_component,
                    _to_json(metadata),
                ),
            )
            connection.commit()

    def record_daily_pnl(self, trading_day: date, realized_pnl: float, unrealized_pnl: float, total_fees: float) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO daily_pnl(trading_day, realized_pnl, unrealized_pnl, total_fees)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trading_day) DO UPDATE SET
                    realized_pnl = excluded.realized_pnl,
                    unrealized_pnl = excluded.unrealized_pnl,
                    total_fees = excluded.total_fees
                """,
                (trading_day.isoformat(), realized_pnl, unrealized_pnl, total_fees),
            )
            connection.commit()

    def aggregate_realized_pnl_today(self, trading_day: date, mode: Optional[str] = None) -> float:
        with self.connect() as connection:
            if mode is None:
                row = connection.execute(
                    """
                    SELECT COALESCE(SUM(realized_pnl), 0.0) AS total
                    FROM positions
                    WHERE status = 'closed' AND substr(closed_at, 1, 10) = ?
                    """,
                    (trading_day.isoformat(),),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT COALESCE(SUM(realized_pnl), 0.0) AS total
                    FROM positions
                    WHERE status = 'closed' AND mode = ? AND substr(closed_at, 1, 10) = ?
                    """,
                    (mode, trading_day.isoformat()),
                ).fetchone()
            return 0.0 if row is None else float(row["total"])

    def aggregate_fees(self) -> float:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(fees), 0.0) AS total FROM fills"
            ).fetchone()
            return 0.0 if row is None else float(row["total"])

    def latest_snapshots(self) -> List[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s1.*
                FROM snapshots s1
                INNER JOIN (
                    SELECT ticker, MAX(captured_at) AS max_captured_at
                    FROM snapshots
                    GROUP BY ticker
                ) latest
                ON latest.ticker = s1.ticker AND latest.max_captured_at = s1.captured_at
                """
            ).fetchall()
            return list(rows)

    def recent_calibration_rows(self) -> List[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM calibration_logs ORDER BY created_at DESC"
            ).fetchall()
            return list(rows)

    def recent_forecasts(self) -> List[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM forecasts ORDER BY created_at DESC"
            ).fetchall()
            return list(rows)

    def close_all_open_positions(self, mark_price: float, reason: str, mode: Optional[str] = None) -> int:
        open_positions = self.fetch_open_positions(mode=mode)
        count = 0
        for position in open_positions:
            realized = (mark_price - position.avg_price) * position.contracts
            if position.side == "NO":
                realized = ((1.0 - mark_price) - position.avg_price) * position.contracts
            self.close_position(
                position.id,
                current_mark=mark_price,
                realized_pnl=realized,
                metadata={"close_reason": reason},
            )
            count += 1
        return count
