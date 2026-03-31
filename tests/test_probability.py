"""Unit tests for the probability engine."""

from __future__ import annotations

import unittest

from core.probability import estimate_bucket_probability


class ProbabilityTests(unittest.TestCase):
    def test_probability_outputs_sum_to_one(self) -> None:
        result = estimate_bucket_probability(
            mean_temp_f=72.0,
            sigma_temp_f=4.0,
            bucket_low=69.5,
            bucket_high=74.5,
            p_climo=0.3,
            member_highs=[70.0, 72.0, 74.0, 75.0, 71.0],
        )
        self.assertAlmostEqual(result.p_bucket_yes + result.p_bucket_no, 1.0, places=6)
        self.assertGreater(result.boundary_mass, 0.0)

    def test_degenerate_sigma_uses_floor(self) -> None:
        result = estimate_bucket_probability(
            mean_temp_f=60.0,
            sigma_temp_f=0.0,
            bucket_low=59.5,
            bucket_high=60.5,
            p_climo=0.25,
            member_highs=[60.0, 60.0, 60.0],
            min_sigma_f=0.5,
        )
        self.assertGreater(result.debug["sigma_temp_f"], 0.0)
        self.assertTrue(0.0 <= result.p_bucket_yes <= 1.0)

    def test_missing_mean_and_sigma_falls_back_to_climo(self) -> None:
        result = estimate_bucket_probability(
            mean_temp_f=None,
            sigma_temp_f=None,
            bucket_low=64.5,
            bucket_high=69.5,
            p_climo=0.42,
            member_highs=[],
        )
        self.assertAlmostEqual(result.p_bucket_yes, 0.42, places=6)

    def test_invalid_bucket_raises(self) -> None:
        with self.assertRaises(ValueError):
            estimate_bucket_probability(
                mean_temp_f=60.0,
                sigma_temp_f=1.0,
                bucket_low=70.0,
                bucket_high=65.0,
                p_climo=0.5,
            )


if __name__ == "__main__":
    unittest.main()
