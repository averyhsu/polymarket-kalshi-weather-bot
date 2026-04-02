"""Tests for live demo configuration and broker safety rails."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
import gc
from unittest.mock import patch

from pydantic import ValidationError

from config import (
    DEFAULT_KALSHI_DEMO_API_BASE_URL,
    DEFAULT_KALSHI_PRODUCTION_API_BASE_URL,
    Settings,
)
from core.decision import TradeDecision
from core.sizing import SizingResult
from data.markets import MarketQuote
from db.models import Database
from execution.live import LiveBroker


class LiveDemoConfigTests(unittest.TestCase):
    def test_demo_environment_uses_demo_api_root_by_default(self) -> None:
        settings = Settings(
            _env_file=None,
            cache_dir=".cache",
            db_path="test.sqlite3",
            historical_data_dir="historical_data/backtests",
            kalshi_environment="demo",
        )
        self.assertEqual(settings.effective_kalshi_api_base_url, DEFAULT_KALSHI_DEMO_API_BASE_URL)

    def test_production_environment_uses_production_api_root_by_default(self) -> None:
        settings = Settings(
            _env_file=None,
            cache_dir=".cache",
            db_path="test.sqlite3",
            historical_data_dir="historical_data/backtests",
            kalshi_environment="production",
        )
        self.assertEqual(settings.effective_kalshi_api_base_url, DEFAULT_KALSHI_PRODUCTION_API_BASE_URL)

    def test_mismatched_environment_and_base_url_fails_fast(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                cache_dir=".cache",
                db_path="test.sqlite3",
                historical_data_dir="historical_data/backtests",
                kalshi_environment="demo",
                kalshi_api_base_url=DEFAULT_KALSHI_PRODUCTION_API_BASE_URL,
            )

    def test_live_mode_requires_credentials(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                cache_dir=".cache",
                db_path="test.sqlite3",
                historical_data_dir="historical_data/backtests",
                mode="live",
                kalshi_environment="demo",
            )


class LiveBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_path = Path(tempfile.mkdtemp())
        self.key_path = self.temp_path / "demo-key.pem"
        self.key_path.write_text("demo", encoding="utf-8")
        self.settings = Settings(
            _env_file=None,
            mode="live",
            dry_run=True,
            cache_dir=str(self.temp_path / ".cache"),
            db_path=str(self.temp_path / "trading.sqlite3"),
            historical_data_dir=str(self.temp_path / "historical"),
            kalshi_environment="demo",
            kalshi_api_key_id="demo-key-id",
            kalshi_private_key_path=self.key_path,
            no_only=True,
            yes_enabled=False,
        )
        self.database = Database(self.settings.db_path)
        self.broker = LiveBroker(self.settings, self.database)
        self.market = MarketQuote(
            ticker="KXHIGHNY-26APR02-B70.5",
            series_ticker="KXHIGHNY",
            city_key="nyc",
            city_name="New York City",
            target_date=date(2026, 4, 2),
            bucket_low=69.5,
            bucket_high=float("inf"),
            strike_label="70 or above",
            title="NYC high temp",
            subtitle="70 or above",
            yes_bid=0.32,
            yes_ask=0.34,
            no_bid=0.64,
            no_ask=0.66,
            last_price=0.34,
            volume=100.0,
            open_interest=30.0,
            updated_time=None,
            status="open",
        )
        self.decision = TradeDecision(
            approved=True,
            side="NO",
            price=0.66,
            win_probability=0.74,
            raw_win_probability=0.74,
            expected_value=0.05,
            min_required_ev=0.04,
            ranking_score=0.02,
            yes_ev=-0.2,
            no_ev=0.05,
            calibration_sample_size=0,
            rationale=["NO selected"],
        )
        self.sizing = SizingResult(
            raw_kelly=0.1,
            adjusted_kelly=0.08,
            target_size_usd=5.0,
            capped_size_usd=5.0,
            contracts=3,
            max_position_cap_usd=10.0,
            cost_per_contract=0.67,
            debug={},
        )

    def tearDown(self) -> None:
        del self.broker
        del self.database
        gc.collect()
        for path in sorted(self.temp_path.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        self.temp_path.rmdir()

    def test_preflight_uses_authenticated_probe(self) -> None:
        with patch("execution.live.KalshiClient.preflight_authenticated", return_value={"positions": []}) as mocked:
            result = self.broker.preflight()

        self.assertTrue(result.ok)
        self.assertEqual(result.environment, "demo")
        self.assertEqual(result.api_base_url, DEFAULT_KALSHI_DEMO_API_BASE_URL)
        mocked.assert_called_once()

    def test_dry_run_records_local_order_after_preflight(self) -> None:
        with patch("execution.live.KalshiClient.preflight_authenticated", return_value={"positions": []}) as mocked:
            result = self.broker.place_entry_order(
                decision=self.decision,
                sizing=self.sizing,
                market=self.market,
            )

        self.assertTrue(result.submitted)
        self.assertIsNotNone(result.order_id)
        self.assertIsNone(result.remote_order_id)
        self.assertIn("dry-run", result.message)
        mocked.assert_called_once()

        with self.database.connect() as connection:
            row = connection.execute("SELECT mode, side, status FROM orders").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["mode"], "live")
        self.assertEqual(row["side"], "NO")
        self.assertEqual(row["status"], "dry-run")


if __name__ == "__main__":
    unittest.main()
