"""Unit tests for the decision engine."""

from __future__ import annotations

import unittest
from datetime import date

from config import load_settings
from core.decision import choose_trade
from core.probability import ProbabilityResult
from core.risk import RiskAssessment
from data.markets import MarketQuote


class DecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.market = MarketQuote(
            ticker="KXHIGHNY-26APR01-RANGE",
            series_ticker="KXHIGHNY",
            city_key="nyc",
            city_name="New York City",
            target_date=date(2026, 4, 1),
            bucket_low=69.5,
            bucket_high=74.5,
            strike_label="70 to 74",
            title="NYC high temp",
            subtitle="70 to 74",
            yes_bid=0.38,
            yes_ask=0.40,
            no_bid=0.58,
            no_ask=0.60,
            last_price=0.40,
            volume=100.0,
            open_interest=25.0,
            updated_time=None,
            status="open",
        )
        self.risk = RiskAssessment(
            uncertainty=0.2,
            dynamic_min_ev=0.04,
            size_mult=0.88,
            source_health_mult=1.0,
            source_health_status="HEALTHY",
            allowed=True,
            reasons=[],
        )

    def test_yes_side_selected_when_ev_is_better(self) -> None:
        settings = load_settings({"cache_dir": ".cache", "db_path": "test.sqlite3"})
        probability = ProbabilityResult(0.62, 0.38, 0.10, 0.30, {})
        decision = choose_trade(market=self.market, probability=probability, risk=self.risk, settings=settings)
        self.assertTrue(decision.approved)
        self.assertEqual(decision.side, "YES")

    def test_no_only_mode_rejects_yes_only_edge(self) -> None:
        settings = load_settings({"cache_dir": ".cache", "db_path": "test.sqlite3", "no_only": True})
        probability = ProbabilityResult(0.70, 0.30, 0.05, 0.10, {})
        decision = choose_trade(market=self.market, probability=probability, risk=self.risk, settings=settings)
        self.assertEqual(decision.side, "NONE")
        self.assertFalse(decision.approved)

    def test_rejects_when_risk_is_not_allowed(self) -> None:
        settings = load_settings({"cache_dir": ".cache", "db_path": "test.sqlite3"})
        blocked_risk = RiskAssessment(0.5, 0.05, 0.7, 0.0, "BROKEN", False, ["broken source"])
        probability = ProbabilityResult(0.52, 0.48, 0.1, 0.2, {})
        decision = choose_trade(market=self.market, probability=probability, risk=blocked_risk, settings=settings)
        self.assertFalse(decision.approved)
        self.assertIn("broken source", decision.rationale[0])


if __name__ == "__main__":
    unittest.main()
