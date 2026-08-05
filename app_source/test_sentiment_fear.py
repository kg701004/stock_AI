"""Tests for sentiment and VIX/fear scoring."""

import unittest
from pathlib import Path
from database_utils import database_connection

from sentiment_fear import (
    FearInputs, SentimentInputs, build_sentiment_factors, score_fear,
    score_sentiment, global_risk_factor_score
)


class SentimentFearTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_sentiment_fear_vix.sqlite")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def test_global_risk_factor_score_with_sufficient_data(self) -> None:
        with database_connection(self.database) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS vix_history (
                    trading_date TEXT PRIMARY KEY,
                    value REAL NOT NULL,
                    source TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                )
            """)
            vix_data = [
                ("2026-01-01", 10.0, "FRED:VIXCLS", "2026-01-01T00:00:00"),
                ("2026-01-02", 11.0, "FRED:VIXCLS", "2026-01-02T00:00:00"),
                ("2026-01-03", 12.0, "FRED:VIXCLS", "2026-01-03T00:00:00"),
                ("2026-01-04", 13.0, "FRED:VIXCLS", "2026-01-04T00:00:00"),
                ("2026-01-05", 14.0, "FRED:VIXCLS", "2026-01-05T00:00:00"),
                ("2026-01-06", 15.0, "FRED:VIXCLS", "2026-01-06T00:00:00"),
                ("2026-01-07", 16.0, "FRED:VIXCLS", "2026-01-07T00:00:00"),
            ]
            c.executemany("INSERT INTO vix_history VALUES (?, ?, ?, ?)", vix_data)

        score, note = global_risk_factor_score(self.database)
        self.assertIsNotNone(score)
        self.assertEqual(score, 15.0)
        self.assertIn("16.00", note)
        self.assertIn("歷史百分位 100", note)

    def test_global_risk_factor_score_with_no_database(self) -> None:
        score, note = global_risk_factor_score(Path("test_non_existent.sqlite"))
        self.assertIsNone(score)
        self.assertEqual(note, "VIX資料不足，尚無法計算全球風險因子")

    def test_global_risk_factor_score_with_empty_or_no_table(self) -> None:
        with database_connection(self.database) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS vix_history (
                    trading_date TEXT PRIMARY KEY,
                    value REAL NOT NULL,
                    source TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                )
            """)
        score, note = global_risk_factor_score(self.database)
        self.assertIsNone(score)
        self.assertEqual(note, "VIX資料不足，尚無法計算全球風險因子")

    def test_global_risk_factor_score_with_insufficient_data(self) -> None:
        with database_connection(self.database) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS vix_history (
                    trading_date TEXT PRIMARY KEY,
                    value REAL NOT NULL,
                    source TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                )
            """)
            vix_data = [
                ("2026-01-01", 10.0, "FRED:VIXCLS", "2026-01-01T00:00:00"),
                ("2026-01-02", 11.0, "FRED:VIXCLS", "2026-01-02T00:00:00"),
                ("2026-01-03", 12.0, "FRED:VIXCLS", "2026-01-03T00:00:00"),
                ("2026-01-04", 13.0, "FRED:VIXCLS", "2026-01-04T00:00:00"),
                ("2026-01-05", 14.0, "FRED:VIXCLS", "2026-01-05T00:00:00"),
            ]
            c.executemany("INSERT INTO vix_history VALUES (?, ?, ?, ?)", vix_data)

        score, note = global_risk_factor_score(self.database)
        self.assertIsNone(score)
        self.assertEqual(note, "VIX資料不足，尚無法計算全球風險因子")

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
