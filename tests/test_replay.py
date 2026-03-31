"""Tests for the historical backtest harness."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from analytics.replay import run_historical_backtest
from config import load_settings
from data.markets import HistoricalMarketDefinition, MarketQuote
from data.weather import ForecastSnapshot, SourceHealth


def _settings():
    return load_settings(
        {
            "initial_balance": 100.0,
            "enabled_cities": ["nyc"],
            "blacklisted_cities": [],
        }
    )


class BacktestTests(unittest.TestCase):
    def test_backtest_executes_and_settles_trade(self) -> None:
        settings = _settings()
        target_date = date(2026, 3, 30)
        market_def = HistoricalMarketDefinition(
            ticker="KXHIGHNY-26MAR30-T69",
            series_ticker="KXHIGHNY",
            city_key="nyc",
            city_name="New York City",
            target_date=target_date,
            bucket_low=69.5,
            bucket_high=float("inf"),
            strike_label="70 or above",
            title="NYC high >69",
            subtitle="70 or above",
            actual_high_f=73.0,
            status="finalized",
            open_time=None,
            close_time=None,
            settlement_time=None,
            volume=1000.0,
            open_interest=500.0,
        )
        quote = MarketQuote(
            ticker=market_def.ticker,
            series_ticker=market_def.series_ticker,
            city_key=market_def.city_key,
            city_name=market_def.city_name,
            target_date=market_def.target_date,
            bucket_low=market_def.bucket_low,
            bucket_high=market_def.bucket_high,
            strike_label=market_def.strike_label,
            title=market_def.title,
            subtitle=market_def.subtitle,
            yes_bid=0.18,
            yes_ask=0.20,
            no_bid=0.78,
            no_ask=0.80,
            last_price=0.19,
            volume=market_def.volume,
            open_interest=market_def.open_interest,
            updated_time=datetime(2026, 3, 29, 20, 0, tzinfo=timezone.utc),
            status="settled",
        )
        forecast = ForecastSnapshot(
            city_key="nyc",
            city_name="New York City",
            target_date=target_date,
            fetched_at=datetime(2026, 3, 29, 18, 0, tzinfo=timezone.utc),
            provider="open-meteo-gfs-single-runs",
            member_highs=[72.0, 73.0, 74.0, 72.5],
            mean_temp_f=72.875,
            sigma_temp_f=0.83,
            member_spread_f=2.0,
            disagreement=0.25,
            source_health=SourceHealth(
                score=1.0,
                status="HEALTHY",
                success=1.0,
                freshness=1.0,
                completeness=1.0,
                consistency=1.0,
            ),
        )

        with patch("analytics.replay.fetch_historical_market_definitions", return_value=[market_def]), patch(
            "analytics.replay.fetch_historical_market_quote", return_value=quote
        ), patch("analytics.replay.fetch_historical_forecast_snapshot", return_value=forecast):
            result = run_historical_backtest(settings, start_date=target_date, end_date=target_date)

        summary = result["backtest"]
        self.assertEqual(summary["markets_considered"], 1)
        self.assertEqual(summary["entries_executed"], 1)
        self.assertEqual(summary["settled_positions"], 1)
        self.assertGreater(summary["ending_balance"], summary["starting_balance"])
        self.assertEqual(len(summary["trades"]), 1)
        self.assertGreater(summary["brier_score"], 0.0)

    def test_backtest_skips_missing_historical_quote(self) -> None:
        settings = _settings()
        target_date = date(2026, 3, 30)
        market_def = HistoricalMarketDefinition(
            ticker="KXHIGHNY-26MAR30-T69",
            series_ticker="KXHIGHNY",
            city_key="nyc",
            city_name="New York City",
            target_date=target_date,
            bucket_low=69.5,
            bucket_high=float("inf"),
            strike_label="70 or above",
            title="NYC high >69",
            subtitle="70 or above",
            actual_high_f=73.0,
            status="finalized",
            open_time=None,
            close_time=None,
            settlement_time=None,
            volume=1000.0,
            open_interest=500.0,
        )

        with patch("analytics.replay.fetch_historical_market_definitions", return_value=[market_def]), patch(
            "analytics.replay.fetch_historical_market_quote", return_value=None
        ):
            result = run_historical_backtest(settings, start_date=target_date, end_date=target_date)

        summary = result["backtest"]
        self.assertEqual(summary["entries_executed"], 0)
        self.assertEqual(summary["settled_positions"], 0)
        self.assertTrue(summary["skipped"])


if __name__ == "__main__":
    unittest.main()
