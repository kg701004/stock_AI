"""Tests for offline public-data market-context analysis."""

import unittest

from market_context import MarketSnapshot, assess_market


class MarketContextTests(unittest.TestCase):
    def test_positive_breadth_produces_bullish_context(self) -> None:
        result = assess_market(MarketSnapshot(700, 300, 80, 10, 700, 1000, 1.2, 1.1))
        self.assertEqual(result.regime, "bullish")
        self.assertGreaterEqual(result.score, 65)

    def test_combined_risks_produce_bearish_context(self) -> None:
        result = assess_market(MarketSnapshot(200, 800, 10, 80, 200, 1000, -2, -1.8, -2.1, 30, 1.4))
        self.assertEqual(result.regime, "bearish")
        self.assertEqual(result.risk_level, "high")

    def test_invalid_universe_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MarketSnapshot(1, 1, 0, 0, 0, 0, 0, 0)


if __name__ == "__main__":
    unittest.main()
