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
        settings = load_settings(
            {
                "cache_dir": ".cache",
                "db_path": "test.sqlite3",
                "no_mid_price_filter_enabled": False,
            }
        )
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

    def test_mid_priced_no_gets_rejected_under_filter(self) -> None:
        settings = load_settings({"cache_dir": ".cache", "db_path": "test.sqlite3"})
        market = MarketQuote(
            **{
                **self.market.__dict__,
                "yes_bid": 0.59,
                "yes_ask": 0.62,
                "no_bid": 0.34,
                "no_ask": 0.38,
                "last_price": 0.62,
            }
        )
        probability = ProbabilityResult(0.25, 0.75, 0.05, 0.10, {})
        decision = choose_trade(market=market, probability=probability, risk=self.risk, settings=settings)
        self.assertFalse(decision.approved)
        self.assertIn("filtered mid-price range", decision.rationale[0])

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

    def test_event_basket_selection_avoids_mixed_side_cluster(self) -> None:
        def event_candidate(
            ticker: str,
            *,
            side: str,
            price: float,
            bucket_low: float,
            bucket_high: float,
            probability_yes: float,
            contracts: int,
            ranking_score: float,
            expected_value: float,
        ) -> dict:
            market = MarketQuote(
                **{
                    **self.market.__dict__,
                    "ticker": ticker,
                    "bucket_low": bucket_low,
                    "bucket_high": bucket_high,
                    "yes_bid": max(price - 0.02, 0.01),
                    "yes_ask": price if side == "YES" else 0.40,
                    "no_bid": 0.58 if side == "NO" else max(1.0 - price - 0.03, 0.01),
                    "no_ask": price if side == "NO" else max(1.0 - price, 0.01),
                    "last_price": price,
                }
            )
            return {
                "ticker": ticker,
                "city_key": "nyc",
                "target_date": "2026-04-01",
                "market": market,
                "decision": SimpleNamespace(
                    side=side,
                    price=price,
                    ranking_score=ranking_score,
                    expected_value=expected_value,
                ),
                "sizing": SimpleNamespace(contracts=contracts),
                "probability": ProbabilityResult(probability_yes, 1.0 - probability_yes, 0.05, 0.10, {}),
            }

        candidates = [
            event_candidate(
                "yes-tail",
                side="YES",
                price=0.21,
                bucket_low=float("-inf"),
                bucket_high=46.5,
                probability_yes=0.60,
                contracts=7,
                ranking_score=0.80,
                expected_value=0.30,
            ),
            event_candidate(
                "no-mid-a",
                side="NO",
                price=0.74,
                bucket_low=46.5,
                bucket_high=48.5,
                probability_yes=0.08,
                contracts=2,
                ranking_score=0.35,
                expected_value=0.12,
            ),
            event_candidate(
                "no-mid-b",
                side="NO",
                price=0.72,
                bucket_low=48.5,
                bucket_high=50.5,
                probability_yes=0.10,
                contracts=2,
                ranking_score=0.34,
                expected_value=0.11,
            ),
        ]

        selected, rejected = select_ranked_candidates(
            candidates,
            remaining_slots=3,
            max_positions_per_city_day=3,
            fee_per_contract=0.01,
            slippage_per_contract=0.005,
            max_yes_positions_per_city_day=1,
            allow_mixed_sides_per_city_day=False,
            event_worst_case_penalty=0.35,
        )
        self.assertTrue(selected)
        self.assertEqual(len({item["decision"].side for item in selected}), 1)
        self.assertLess(len(selected), 3)
        self.assertTrue(any("different bundle" in reason for _, reason in rejected))

    def test_event_basket_selection_limits_yes_to_one_per_city_day(self) -> None:
        def yes_candidate(ticker: str, price: float, probability_yes: float, ranking_score: float) -> dict:
            market = MarketQuote(
                **{
                    **self.market.__dict__,
                    "ticker": ticker,
                    "yes_bid": max(price - 0.01, 0.01),
                    "yes_ask": price,
                    "no_bid": max(1.0 - price - 0.02, 0.01),
                    "no_ask": max(1.0 - price, 0.01),
                    "last_price": price,
                }
            )
            return {
                "ticker": ticker,
                "city_key": "nyc",
                "target_date": "2026-04-01",
                "market": market,
                "decision": SimpleNamespace(
                    side="YES",
                    price=price,
                    ranking_score=ranking_score,
                    expected_value=0.20,
                ),
                "sizing": SimpleNamespace(contracts=4),
                "probability": ProbabilityResult(probability_yes, 1.0 - probability_yes, 0.05, 0.10, {}),
            }

        selected, rejected = select_ranked_candidates(
            [
                yes_candidate("yes-a", 0.28, 0.55, 0.40),
                yes_candidate("yes-b", 0.24, 0.58, 0.45),
            ],
            remaining_slots=3,
            max_positions_per_city_day=3,
            fee_per_contract=0.01,
            slippage_per_contract=0.005,
            max_yes_positions_per_city_day=1,
            allow_mixed_sides_per_city_day=False,
            event_worst_case_penalty=0.35,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["ticker"], "yes-b")
        self.assertEqual(rejected[0][0]["ticker"], "yes-a")

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
