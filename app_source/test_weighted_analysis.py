"""Tests for user-configurable scoring and its audit trail."""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from database_utils import database_connection
from weighted_analysis import AnalysisInput, FACTOR_NAMES, assess_stock, load_weight_config, persist_assessment


class WeightedAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_weight_config(Path("config/analysis_weights.json"))
        self.data = AnalysisInput("2330", datetime(2026, 7, 22, 13, 30, tzinfo=timezone.utc), {factor: 80 for factor in FACTOR_NAMES}, 20, {"technical": "trend is constructive"})

    def test_weights_are_normalized_and_visible(self) -> None:
        result = assess_stock(self.data, self.config)
        self.assertAlmostEqual(sum(item.normalized_weight for item in result.contributions), 1.0, places=5)
        self.assertGreater(result.final_score, 70)
        self.assertEqual(result.classification, "strong_watch")

    def test_invalid_weight_schema_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            load_weight_config(Path("config/invalid_weights_fixture.json"))

    def test_assessment_is_persisted_with_all_factors(self) -> None:
        result = assess_stock(self.data, self.config)
        database = Path("test_decision_audit.sqlite")
        persist_assessment(database, result)
        with database_connection(database) as connection:
            self.assertGreaterEqual(connection.execute("SELECT COUNT(*) FROM assessments").fetchone()[0], 1)
            self.assertGreaterEqual(connection.execute("SELECT COUNT(*) FROM assessment_contributions").fetchone()[0], len(FACTOR_NAMES))


if __name__ == "__main__":
    unittest.main()
