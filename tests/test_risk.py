"""Unit tests for risk scoring."""

from __future__ import annotations

import unittest

from config import load_settings
from core.risk import assess_entry_risk, uncertainty_score
from data.weather import SourceHealth


class RiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_settings({"cache_dir": ".cache", "db_path": "test.sqlite3"})
        self.source_health = SourceHealth(
            score=0.9,
            status="HEALTHY",
            success=1.0,
            freshness=1.0,
            completeness=1.0,
            consistency=0.8,
        )

    def test_uncertainty_score_is_weighted_and_clamped(self) -> None:
        score = uncertainty_score(0.5, 1.0, self.settings)
        self.assertTrue(0.0 <= score <= 1.0)
        self.assertGreater(score, 0.8)

    def test_risk_blocks_wide_spreads(self) -> None:
        risk = assess_entry_risk(
            settings=self.settings,
            boundary_mass=0.05,
            disagreement=0.2,
            spread_cents=12,
            source_health=self.source_health,
            open_positions=0,
            realized_pnl_today=0.0,
            bankroll_reference=100.0,
        )
        self.assertFalse(risk.allowed)
        self.assertIn("spread", risk.reasons[0])

    def test_risk_blocks_daily_loss(self) -> None:
        risk = assess_entry_risk(
            settings=self.settings,
            boundary_mass=0.05,
            disagreement=0.2,
            spread_cents=2,
            source_health=self.source_health,
            open_positions=0,
            realized_pnl_today=-20.0,
            bankroll_reference=100.0,
        )
        self.assertFalse(risk.allowed)
        self.assertTrue(any("daily max loss" in reason for reason in risk.reasons))


if __name__ == "__main__":
    unittest.main()
