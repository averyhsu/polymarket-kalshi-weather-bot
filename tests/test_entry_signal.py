"""Tests for entry-signal calibration, side filters, and ranking."""

from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from config import load_settings
from core.calibration import CalibrationContext, CalibrationSample
from core.decision import choose_trade
from core.entry_selection import select_ranked_candidates
from core.probability import ProbabilityResult
from core.risk import RiskAssessment
from data.markets import MarketQuote


class EntrySignalTests(unittest.TestCase):
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
            yes_bid=0.03,
            yes_ask=0.05,
            no_bid=0.93,
            no_ask=0.95,
            last_price=0.05,
            volume=100.0,
            open_interest=25.0,
            updated_time=None,
            status="open",
        )
        self.risk = RiskAssessment(
            uncertainty=0.20,
            dynamic_min_ev=0.04,
            size_mult=0.88,
            source_health_mult=1.0,
            source_health_status="HEALTHY",
            allowed=True,
            reasons=[],
        )

    def test_cheap_yes_gets_rejected_under_floor(self) -> None:
        settings = load_settings({"cache_dir": ".cache", "db_path": "test.sqlite3"})
        probability = ProbabilityResult(0.70, 0.30, 0.05, 0.10, {})
        decision = choose_trade(market=self.market, probability=probability, risk=self.risk, settings=settings)
        self.assertFalse(decision.approved)
        self.assertIn("YES price 5c below minimum 10c", decision.rationale[0])

    def test_yes_requires_higher_ev_than_no(self) -> None:
        settings = load_settings({"cache_dir": ".cache", "db_path": "test.sqlite3"})
        market = MarketQuote(
            **{
                **self.market.__dict__,
                "yes_bid": 0.33,
                "yes_ask": 0.36,
                "no_bid": 0.60,
                "no_ask": 0.38,
                "last_price": 0.36,
            }
        )
        probability = ProbabilityResult(0.45, 0.55, 0.05, 0.10, {})
        decision = choose_trade(market=market, probability=probability, risk=self.risk, settings=settings)
        self.assertTrue(decision.approved)
        self.assertEqual(decision.side, "NO")

    def test_city_override_can_reject_otherwise_valid_entry(self) -> None:
        settings = load_settings(
            {"cache_dir": ".cache", "db_path": "test.sqlite3", "city_ev_buffer_overrides": {"nyc": 0.20}}
        )
        market = MarketQuote(
            **{
                **self.market.__dict__,
                "yes_bid": 0.28,
                "yes_ask": 0.30,
                "no_bid": 0.68,
                "no_ask": 0.70,
                "last_price": 0.30,
            }
        )
        probability = ProbabilityResult(0.42, 0.58, 0.05, 0.10, {})
        decision = choose_trade(market=market, probability=probability, risk=self.risk, settings=settings)
        self.assertFalse(decision.approved)
        self.assertIn("below dynamic minimum", decision.rationale[0])

    def test_tail_penalty_can_block_fragile_yes_trade(self) -> None:
        settings = load_settings(
            {
                "cache_dir": ".cache",
                "db_path": "test.sqlite3",
                "yes_min_ev": 0.02,
                "tail_risk_penalty": 0.03,
                "tail_yes_price_cents": 12,
                "tail_yes_probability_threshold": 0.58,
                "tail_uncertainty_threshold": 0.35,
            }
        )
        risk = RiskAssessment(0.40, 0.04, 0.88, 1.0, "HEALTHY", True, [])
        market = MarketQuote(
            **{
                **self.market.__dict__,
                "yes_bid": 0.10,
                "yes_ask": 0.12,
                "no_bid": 0.86,
                "no_ask": 0.88,
                "last_price": 0.12,
            }
        )
        probability = ProbabilityResult(0.60, 0.40, 0.05, 0.10, {})
        samples = [
            CalibrationSample(city_key="nyc", side="YES", price=0.12, predicted_win_probability=0.60, settled_win=1.0)
            for _ in range(2)
        ] + [
            CalibrationSample(city_key="nyc", side="YES", price=0.12, predicted_win_probability=0.60, settled_win=0.0)
            for _ in range(8)
        ]
        calibration = CalibrationContext(samples, pseudo_count=0.0)
        decision = choose_trade(
            market=market,
            probability=probability,
            risk=risk,
            settings=settings,
            calibration=calibration,
        )
        self.assertFalse(decision.approved)
        self.assertIn("tail-risk penalty", " ".join(decision.rationale))

    def test_candidate_selection_uses_ranking_not_scan_order(self) -> None:
        def candidate(ticker: str, ranking_score: float, city: str = "nyc") -> dict:
            return {
                "ticker": ticker,
                "city_key": city,
                "target_date": "2026-04-01",
                "market": SimpleNamespace(spread_cents=2),
                "decision": SimpleNamespace(ranking_score=ranking_score, expected_value=ranking_score),
            }

        candidates = [candidate("low", 0.10), candidate("high", 0.30), candidate("mid", 0.20)]
        selected, rejected = select_ranked_candidates(
            candidates,
            remaining_slots=2,
            max_positions_per_city_day=3,
            existing_city_day_counts={},
        )
        self.assertEqual([item["ticker"] for item in selected], ["high", "mid"])
        self.assertEqual(rejected[0][0]["ticker"], "low")

    def test_calibration_context_adjusts_probability_and_stays_bounded(self) -> None:
        samples = [
            CalibrationSample(city_key="nyc", side="YES", price=0.12, predicted_win_probability=0.60, settled_win=1.0)
            for _ in range(2)
        ] + [
            CalibrationSample(city_key="nyc", side="YES", price=0.12, predicted_win_probability=0.60, settled_win=0.0)
            for _ in range(8)
        ]
        context = CalibrationContext(samples, pseudo_count=0.0)
        adjustment = context.calibrate(side="YES", city_key="nyc", price=0.12, raw_probability=0.60)
        self.assertGreaterEqual(adjustment.calibrated_probability, 0.0)
        self.assertLessEqual(adjustment.calibrated_probability, 1.0)
        self.assertLess(adjustment.calibrated_probability, 0.60)


if __name__ == "__main__":
    unittest.main()
