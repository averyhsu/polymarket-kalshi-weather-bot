"""Tests for Kalshi market normalization and historical retrieval."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from config import load_settings
from data.markets import fetch_historical_market_definitions


def _settings():
    return load_settings(
        {
            "cache_dir": ".cache",
            "db_path": "test.sqlite3",
            "enabled_cities": ["chicago"],
            "blacklisted_cities": [],
        }
    )


class HistoricalMarketFetchTests(unittest.TestCase):
    def test_fetch_historical_market_definitions_uses_settled_events(self) -> None:
        settings = _settings()
        payload = {
            "cursor": None,
            "events": [
                {
                    "event_ticker": "KXHIGHCHI-26MAR30",
                    "series_ticker": "KXHIGHCHI",
                    "strike_date": "2026-03-30",
                    "markets": [
                        {
                            "ticker": "KXHIGHCHI-26MAR30-B74.5",
                            "series_ticker": "KXHIGHCHI",
                            "title": "Will the high temp in Chicago be 74-75° on Mar 30, 2026?",
                            "subtitle": "74° to 75°",
                            "expiration_value": "81.00",
                            "status": "finalized",
                            "open_time": "2026-03-29T14:00:00Z",
                            "settlement_ts": "2026-03-31T12:01:58.02723Z",
                            "volume_fp": "15933.00",
                            "open_interest_fp": "11446.00",
                        },
                        {
                            "ticker": "KXHIGHCHI-26MAR30-T74",
                            "series_ticker": "KXHIGHCHI",
                            "title": "Will the high temp in Chicago be <74° on Mar 30, 2026?",
                            "subtitle": "73° or below",
                            "expiration_value": "81.00",
                            "status": "finalized",
                            "open_time": "2026-03-29T14:00:00Z",
                            "settlement_ts": "2026-03-31T12:01:58.02723Z",
                            "volume_fp": "28225.00",
                            "open_interest_fp": "21337.00",
                        },
                    ],
                }
            ],
        }

        with patch("data.markets.KalshiClient.list_events", return_value=payload) as list_events:
            markets = fetch_historical_market_definitions(
                settings,
                start_date=date(2026, 3, 30),
                end_date=date(2026, 3, 30),
            )

        self.assertEqual(list_events.call_count, 1)
        self.assertEqual([market.ticker for market in markets], ["KXHIGHCHI-26MAR30-B74.5", "KXHIGHCHI-26MAR30-T74"])
        self.assertTrue(all(market.target_date == date(2026, 3, 30) for market in markets))
        self.assertTrue(all(market.city_key == "chicago" for market in markets))


if __name__ == "__main__":
    unittest.main()
