"""Unit tests for Kelly sizing."""

from __future__ import annotations

import unittest

from config import load_settings
from core.sizing import calculate_kelly_size


class SizingTests(unittest.TestCase):
    def test_negative_kelly_is_clipped(self) -> None:
        settings = load_settings({"cache_dir": ".cache", "db_path": "test.sqlite3"})
        result = calculate_kelly_size(
            balance=100.0,
            probability=0.30,
            cost=0.70,
            fee=0.01,
            uncertainty_mult=1.0,
            source_health_mult=1.0,
            settings=settings,
        )
        self.assertEqual(result.contracts, 0)
        self.assertEqual(result.adjusted_kelly, 0.0)

    def test_balanced_profile_returns_position_size(self) -> None:
        settings = load_settings({"cache_dir": ".cache", "db_path": "test.sqlite3"})
        result = calculate_kelly_size(
            balance=500.0,
            probability=0.62,
            cost=0.40,
            fee=0.01,
            uncertainty_mult=0.8,
            source_health_mult=1.0,
            settings=settings,
        )
        self.assertGreaterEqual(result.contracts, 1)
        self.assertLessEqual(result.capped_size_usd, settings.max_position_usd)

    def test_conservative_profile_dampens_size(self) -> None:
        balanced = load_settings({"cache_dir": ".cache", "db_path": "balanced.sqlite3"})
        conservative = load_settings({"cache_dir": ".cache", "db_path": "conservative.sqlite3", "profile": "conservative"})
        balanced_size = calculate_kelly_size(
            balance=500.0,
            probability=0.62,
            cost=0.40,
            fee=0.01,
            uncertainty_mult=1.0,
            source_health_mult=1.0,
            settings=balanced,
        )
        conservative_size = calculate_kelly_size(
            balance=500.0,
            probability=0.62,
            cost=0.40,
            fee=0.01,
            uncertainty_mult=1.0,
            source_health_mult=1.0,
            settings=conservative,
        )
        self.assertLessEqual(conservative_size.capped_size_usd, balanced_size.capped_size_usd)


if __name__ == "__main__":
    unittest.main()
