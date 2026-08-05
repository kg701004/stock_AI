"""Tests for portfolio-level exposure controls."""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from portfolio import Position
from portfolio_risk import SecurityMetadata, assess_owner_portfolio, load_risk_rules, pearson_correlation, stress_correlation


class PortfolioRiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = load_risk_rules(Path("config/portfolio_risk_rules.json"))
        timestamp = datetime(2026, 7, 22, tzinfo=timezone.utc)
        self.positions = [Position("Will", "2330", 1000, 100, 120, timestamp), Position("Will", "2317", 500, 100, 100, timestamp)]
        self.metadata = {"2330": SecurityMetadata("2330", "半導體", 1.2), "2317": SecurityMetadata("2317", "電子製造", 1.0)}

    def test_concentration_warning_is_generated(self) -> None:
        report = assess_owner_portfolio("Will", self.positions, self.metadata, self.rules)
        self.assertAlmostEqual(report.holding_weights_pct["2330"], 70.59, places=2)
        self.assertTrue(any("2330" in warning for warning in report.warnings))

    def test_correlation(self) -> None:
        self.assertAlmostEqual(pearson_correlation([1, 2, 3, 4], [2, 4, 6, 8]), 1.0)

    def test_high_correlation_is_reported(self) -> None:
        report = assess_owner_portfolio("Will", self.positions, self.metadata, self.rules, {"2330": [0.01, 0.02, 0.03], "2317": [0.02, 0.04, 0.06]})
        self.assertEqual(report.high_correlation_pairs[0][:2], ("2330", "2317"))

    def test_stress_correlation_converges_toward_one(self) -> None:
        self.assertAlmostEqual(stress_correlation(0.4, 0.5), 0.7)

    def test_stress_correlation_pulls_negative_correlation_upward(self) -> None:
        self.assertAlmostEqual(stress_correlation(-0.5, 0.5), 0.25)

    def test_stress_correlation_zero_shock_is_a_no_op(self) -> None:
        self.assertEqual(stress_correlation(0.4, 0.0), 0.4)

    def test_stress_correlation_full_shock_reaches_one(self) -> None:
        self.assertEqual(stress_correlation(-0.9, 1.0), 1.0)

    def test_stress_correlation_rejects_out_of_range_inputs(self) -> None:
        with self.assertRaises(ValueError):
            stress_correlation(1.5, 0.5)
        with self.assertRaises(ValueError):
            stress_correlation(0.5, 1.5)


if __name__ == "__main__":
    unittest.main()
