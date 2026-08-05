"""Tests for sentiment and VIX/fear scoring."""

import unittest

from sentiment_fear import FearInputs, SentimentInputs, build_sentiment_factors, score_fear, score_sentiment


class SentimentFearTests(unittest.TestCase):
    def test_constructive_sentiment_scores_above_neutral(self) -> None:
        result = score_sentiment(SentimentInputs(700, 300, 80, 10, 30, 2, 0.7, 70, 65))
        self.assertGreater(result.score, 70)

    def test_extreme_fear_scores_below_neutral(self) -> None:
        result = score_fear(FearInputs(35, 95, 25, -2, 1.4))
        self.assertLess(result.score, 20)

    def test_factors_have_expected_names(self) -> None:
        factors, notes = build_sentiment_factors(SentimentInputs(500, 500, 20, 20, 3, 3), FearInputs(18, 30, -5))
        self.assertEqual(set(factors), {"sentiment", "global_risk"})
        self.assertEqual(set(notes), {"sentiment", "global_risk"})


if __name__ == "__main__":
    unittest.main()
