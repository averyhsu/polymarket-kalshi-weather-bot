"""Tests for CLI backtest window resolution."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from data.markets import earliest_reconstructable_market_date
from data.weather import earliest_historical_forecast_date
from main import _resolve_backtest_window, build_parser


class BacktestWindowTests(unittest.TestCase):
    def test_backtest_max_range_resolves_to_earliest_supported_history(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--backtest", "--backtest-max-range"])
        with patch("main.date", wraps=date) as mocked_date:
            mocked_date.today.return_value = date(2026, 3, 31)
            start_date, end_date = _resolve_backtest_window(args, parser)

        self.assertEqual(
            start_date,
            max(earliest_historical_forecast_date(), earliest_reconstructable_market_date()),
        )
        self.assertEqual(end_date, date(2026, 3, 30))


if __name__ == "__main__":
    unittest.main()
