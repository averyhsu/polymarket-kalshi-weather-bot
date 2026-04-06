"""Tests for the historical backtest harness."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
import shutil
from unittest.mock import patch
from uuid import uuid4

from analytics.replay import (
    BacktestPosition,
    HistoricalBacktestCache,
    _as_position_record,
    _candlestick_to_quote,
    _exit_position,
    _simulate_exit_checkpoints,
    run_historical_backtest,
    warm_historical_backtest_cache,
)
from config import load_settings
from data.markets import CandlestickPoint, HistoricalMarketDefinition, MarketQuote
from data.weather import ForecastSnapshot, SourceHealth


def _settings(cache_dir: Path | None = None):
    overrides = {
        "initial_balance": 100.0,
        "enabled_cities": ["nyc"],
        "blacklisted_cities": [],
        "no_only": False,
        "yes_enabled": True,
    }
    if cache_dir is not None:
        overrides["cache_dir"] = cache_dir
        overrides["historical_data_dir"] = cache_dir / "historical_data"
    return load_settings(overrides)


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
            result = run_historical_backtest(settings, start_date=target_date, end_date=target_date, use_cache=False)

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
            result = run_historical_backtest(settings, start_date=target_date, end_date=target_date, use_cache=False)

        summary = result["backtest"]
        self.assertEqual(summary["entries_executed"], 0)
        self.assertEqual(summary["settled_positions"], 0)
        self.assertTrue(summary["skipped"])

    def test_backtest_uses_cached_dataset_on_second_run(self) -> None:
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

        cache_dir = Path(".cache") / "unit-test-cache" / str(uuid4())
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            settings = _settings(cache_dir)
            with patch("analytics.replay.fetch_historical_market_definitions", return_value=[market_def]) as markets_mock, patch(
                "analytics.replay.fetch_historical_market_quote", return_value=quote
            ) as quote_mock, patch("analytics.replay.fetch_historical_forecast_snapshot", return_value=forecast) as forecast_mock:
                warm_historical_backtest_cache(settings, start_date=target_date, end_date=target_date)
                self.assertGreater(markets_mock.call_count, 0)
                self.assertGreater(quote_mock.call_count, 0)
                self.assertGreater(forecast_mock.call_count, 0)

            with patch("analytics.replay.fetch_historical_market_definitions", side_effect=AssertionError("markets refetched")), patch(
                "analytics.replay.fetch_historical_market_quote", side_effect=AssertionError("quotes refetched")
            ), patch(
                "analytics.replay.fetch_historical_forecast_snapshot", side_effect=AssertionError("forecasts refetched")
            ):
                result = run_historical_backtest(settings, start_date=target_date, end_date=target_date)
        finally:
            shutil.rmtree(cache_dir, ignore_errors=True)

        summary = result["backtest"]
        self.assertEqual(summary["entries_executed"], 1)
        self.assertTrue(summary["cache_used"])

    def test_backtest_invalidates_legacy_market_cache_files(self) -> None:
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

        cache_dir = Path(".cache") / "unit-test-cache" / str(uuid4())
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            settings = _settings(cache_dir)
            cache = HistoricalBacktestCache(settings, entry_hour_utc=20, entry_minute_utc=0)
            legacy_path = cache.markets_dir / f"{target_date.isoformat()}.json"
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text(
                json.dumps(
                    {
                        "target_date": target_date.isoformat(),
                        "cached_at": "2026-04-02T00:00:00+00:00",
                        "markets": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch("analytics.replay.fetch_historical_market_definitions", return_value=[market_def]) as markets_mock, patch(
                "analytics.replay.fetch_historical_market_quote", return_value=quote
            ), patch("analytics.replay.fetch_historical_forecast_snapshot", return_value=forecast):
                result = run_historical_backtest(settings, start_date=target_date, end_date=target_date)
        finally:
            shutil.rmtree(cache_dir, ignore_errors=True)

        self.assertGreater(markets_mock.call_count, 0)
        self.assertEqual(result["backtest"]["entries_executed"], 1)


def _make_position(**overrides) -> BacktestPosition:
    # target_date is March 31 so the entry at March 29 20:00 UTC is ~28h before event,
    # well outside the 2-hour closeout window.
    defaults = {
        "ticker": "KXHIGHNY-26MAR31-T69",
        "city_key": "nyc",
        "target_date": date(2026, 3, 31),
        "side": "NO",
        "contracts": 2,
        "entry_price": 0.80,
        "entry_fees": 0.02,
        "probability_yes": 0.15,
        "expected_value": 0.05,
        "bucket_low": 69.5,
        "bucket_high": float("inf"),
        "actual_high_f": 73.0,
        "entry_time": datetime(2026, 3, 29, 20, 0, tzinfo=timezone.utc),
        "series_ticker": "KXHIGHNY",
        "use_historical_api": False,
        "market_close_time": datetime(2026, 3, 31, 6, 0, tzinfo=timezone.utc),
        "city_name": "New York City",
        "strike_label": "70 or above",
        "title": "NYC high >69",
        "subtitle": "70 or above",
    }
    defaults.update(overrides)
    return BacktestPosition(**defaults)


def _make_candle(hour_offset: int, yes_bid: float, yes_ask: float, entry_time=None) -> CandlestickPoint:
    base = entry_time or datetime(2026, 3, 29, 20, 0, tzinfo=timezone.utc)
    ts = base + __import__("datetime").timedelta(hours=hour_offset)
    return CandlestickPoint(
        timestamp=ts,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=max(0.0, 1.0 - yes_ask),
        no_ask=max(0.0, 1.0 - yes_bid),
        last_price=(yes_bid + yes_ask) / 2,
        volume=100.0,
        open_interest=500.0,
    )


class ExitSimulationUnitTests(unittest.TestCase):
    """Unit tests for exit simulation helper functions."""

    def test_as_position_record_adapts_fields(self) -> None:
        position = _make_position()
        record = _as_position_record(position)
        self.assertEqual(record.side, "NO")
        self.assertAlmostEqual(record.avg_price, 0.80)
        self.assertAlmostEqual(record.metadata["entry_probability_yes"], 0.15)

    def test_candlestick_to_quote_builds_valid_quote(self) -> None:
        position = _make_position()
        candle = _make_candle(1, yes_bid=0.20, yes_ask=0.25)
        quote = _candlestick_to_quote(candle, position)
        self.assertEqual(quote.ticker, position.ticker)
        self.assertAlmostEqual(quote.yes_bid, 0.20)
        self.assertAlmostEqual(quote.no_bid, 0.75)
        self.assertEqual(quote.target_date, position.target_date)

    def test_stop_loss_triggers_early_exit(self) -> None:
        """A NO position should stop out when the YES bid rises (NO bid drops)."""
        position = _make_position(side="NO", entry_price=0.80)
        settings = _settings()
        # NO bid = 1 - yes_ask. Entry at 0.80, stop-loss at -15c means exit at no_bid <= 0.65.
        # If yes_ask = 0.40 then no_bid = 0.60, which is 20c below 0.80 → triggers stop.
        candles = [
            _make_candle(1, yes_bid=0.18, yes_ask=0.22),  # no_bid=0.78, ok
            _make_candle(2, yes_bid=0.25, yes_ask=0.30),  # no_bid=0.70, ok
            _make_candle(3, yes_bid=0.35, yes_ask=0.40),  # no_bid=0.60, -20c → stop
            _make_candle(4, yes_bid=0.45, yes_ask=0.50),  # shouldn't reach this
        ]
        result = _simulate_exit_checkpoints(position, candles, settings)
        self.assertIsNotNone(result)
        self.assertEqual(result["exit_reason"], "stop loss")
        self.assertEqual(result["exit_time"], candles[2].timestamp)

    def test_profit_take_triggers_early_exit(self) -> None:
        """A NO position should take profit when the NO bid rises enough."""
        # Use high probability_yes so that held_probability (1 - 0.05 = 0.95) keeps EV
        # positive even at high no_bid, preventing "expected value gone" from firing first.
        position = _make_position(side="NO", entry_price=0.80, probability_yes=0.05)
        settings = _settings()
        # Profit-take at +10c means exit when no_bid >= 0.90.
        # no_bid = 1 - yes_ask. If yes_ask = 0.08 then no_bid = 0.92, which is +12c.
        candles = [
            _make_candle(1, yes_bid=0.18, yes_ask=0.22),  # no_bid=0.78, still down
            _make_candle(2, yes_bid=0.05, yes_ask=0.08),  # no_bid=0.92, +12c → take profit
        ]
        result = _simulate_exit_checkpoints(position, candles, settings)
        self.assertIsNotNone(result)
        self.assertEqual(result["exit_reason"], "profit take")

    def test_no_exit_returns_none(self) -> None:
        """Stable prices should produce no exit."""
        position = _make_position(side="NO", entry_price=0.80)
        settings = _settings()
        candles = [
            _make_candle(1, yes_bid=0.18, yes_ask=0.22),  # no_bid=0.78
            _make_candle(2, yes_bid=0.19, yes_ask=0.22),  # no_bid=0.78
            _make_candle(3, yes_bid=0.17, yes_ask=0.21),  # no_bid=0.79
        ]
        result = _simulate_exit_checkpoints(position, candles, settings)
        self.assertIsNone(result)

    def test_exit_position_calculates_pnl_with_exit_fees(self) -> None:
        position = _make_position(side="NO", entry_price=0.80, contracts=2, entry_fees=0.02)
        settings = _settings()
        result = _exit_position(position, exit_price=0.60, exit_reason="stop loss",
                                exit_time=datetime(2026, 3, 30, 0, 0, tzinfo=timezone.utc), settings=settings)
        # realized = (0.60 - 0.80) * 2 - 0.02 (entry_fees) - 0.02 (exit_fees) = -0.44
        self.assertTrue(result["exited_early"])
        self.assertEqual(result["exit_reason"], "stop loss")
        self.assertAlmostEqual(result["realized_pnl"], -0.44, places=4)
        self.assertAlmostEqual(result["fees"], 0.04)

    def test_candles_at_or_before_entry_are_skipped(self) -> None:
        """Candles at or before entry_time should not trigger exits."""
        position = _make_position(side="NO", entry_price=0.80)
        settings = _settings()
        # Candle at hour 0 (== entry time) should be skipped even if prices are extreme.
        candles = [
            _make_candle(0, yes_bid=0.90, yes_ask=0.95),  # at entry time, skipped
        ]
        result = _simulate_exit_checkpoints(position, candles, settings)
        self.assertIsNone(result)

    def test_empty_candles_returns_none(self) -> None:
        position = _make_position()
        settings = _settings()
        result = _simulate_exit_checkpoints(position, [], settings)
        self.assertIsNone(result)


class ExitSimulationIntegrationTests(unittest.TestCase):
    """Integration test: full backtest with simulate_exits enabled."""

    def test_backtest_with_exits_disabled_matches_default(self) -> None:
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
                score=1.0, status="HEALTHY", success=1.0, freshness=1.0,
                completeness=1.0, consistency=1.0,
            ),
        )

        with patch("analytics.replay.fetch_historical_market_definitions", return_value=[market_def]), patch(
            "analytics.replay.fetch_historical_market_quote", return_value=quote
        ), patch("analytics.replay.fetch_historical_forecast_snapshot", return_value=forecast):
            result_default = run_historical_backtest(
                settings, start_date=target_date, end_date=target_date, use_cache=False,
            )
            result_exits_off = run_historical_backtest(
                settings, start_date=target_date, end_date=target_date, use_cache=False,
                simulate_exits=False,
            )

        self.assertEqual(
            result_default["backtest"]["ending_balance"],
            result_exits_off["backtest"]["ending_balance"],
        )
        self.assertFalse(result_default["backtest"]["simulate_exits"])
        self.assertEqual(result_default["backtest"]["early_exits"], 0)

    def test_backtest_with_exits_records_early_exit(self) -> None:
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
            close_time=datetime(2026, 3, 30, 6, 0, tzinfo=timezone.utc),
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
                score=1.0, status="HEALTHY", success=1.0, freshness=1.0,
                completeness=1.0, consistency=1.0,
            ),
        )

        # Build a candlestick series where the NO side gets stopped out.
        # Entry at 0.80, stop-loss triggers when NO bid drops 15c+ (to 0.65 or below).
        # NO bid = 1 - yes_ask, so yes_ask=0.45 → no_bid=0.55, which is -25c → stop.
        # First candle at 21:00 = 3h before midnight March 30 (above 2h closeout threshold).
        stop_candles = [
            CandlestickPoint(
                timestamp=datetime(2026, 3, 29, 21, 0, tzinfo=timezone.utc),
                yes_bid=0.40, yes_ask=0.45, no_bid=0.55, no_ask=0.60,
                last_price=0.42, volume=100, open_interest=500,
            ),
        ]

        with patch("analytics.replay.fetch_historical_market_definitions", return_value=[market_def]), \
             patch("analytics.replay.fetch_historical_market_quote", return_value=quote), \
             patch("analytics.replay.fetch_historical_forecast_snapshot", return_value=forecast), \
             patch("analytics.replay.fetch_historical_candlestick_series", return_value=stop_candles):
            result = run_historical_backtest(
                settings, start_date=target_date, end_date=target_date,
                use_cache=False, simulate_exits=True,
            )

        summary = result["backtest"]
        self.assertTrue(summary["simulate_exits"])
        self.assertEqual(summary["early_exits"], 1)
        self.assertTrue(summary["early_exit_reasons"])
        trade = summary["trades"][0]
        self.assertTrue(trade["exited_early"])
        self.assertIsNotNone(trade["exit_reason"])
        self.assertIsNotNone(trade["exit_time"])


class CandlestickCacheTests(unittest.TestCase):
    """Test candlestick series cache round-trip."""

    def test_save_and_load_candlestick_series(self) -> None:
        cache_dir = Path(".cache") / "unit-test-cache" / str(uuid4())
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            settings = _settings(cache_dir)
            cache = HistoricalBacktestCache(settings, entry_hour_utc=20, entry_minute_utc=0)
            series = [
                CandlestickPoint(
                    timestamp=datetime(2026, 3, 29, 21, 0, tzinfo=timezone.utc),
                    yes_bid=0.20, yes_ask=0.25, no_bid=0.75, no_ask=0.80,
                    last_price=0.22, volume=100.0, open_interest=500.0,
                ),
                CandlestickPoint(
                    timestamp=datetime(2026, 3, 29, 22, 0, tzinfo=timezone.utc),
                    yes_bid=0.30, yes_ask=0.35, no_bid=0.65, no_ask=0.70,
                    last_price=0.32, volume=150.0, open_interest=600.0,
                ),
            ]
            cache.save_candlestick_series("KXHIGHNY-26MAR30-T69", series)
            loaded = cache.load_candlestick_series("KXHIGHNY-26MAR30-T69")
            self.assertIsNotNone(loaded)
            self.assertEqual(len(loaded), 2)
            self.assertAlmostEqual(loaded[0].yes_bid, 0.20)
            self.assertAlmostEqual(loaded[1].yes_bid, 0.30)
            self.assertEqual(loaded[0].timestamp, series[0].timestamp)
        finally:
            shutil.rmtree(cache_dir, ignore_errors=True)

    def test_load_missing_candlestick_returns_none(self) -> None:
        cache_dir = Path(".cache") / "unit-test-cache" / str(uuid4())
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            settings = _settings(cache_dir)
            cache = HistoricalBacktestCache(settings, entry_hour_utc=20, entry_minute_utc=0)
            result = cache.load_candlestick_series("NONEXISTENT-TICKER")
            self.assertIsNone(result)
        finally:
            shutil.rmtree(cache_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
