"""Tests for multi-owner P/L and position advice."""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from portfolio import Position, advise_position, calculate_metrics, load_position_rules, persist_position_advice
from weighted_analysis import AnalysisInput, FACTOR_NAMES, assess_stock, load_weight_config


class PortfolioTests(unittest.TestCase):
    def setUp(self) -> None:
        config = load_weight_config(Path("config/analysis_weights.json"))
        self.rules = load_position_rules(Path("config/position_rules.json"))
        self.position = Position("Will", "2330", 1000, 100, 120, datetime(2026, 7, 22, tzinfo=timezone.utc))
        self.strong = assess_stock(AnalysisInput("2330", self.position.as_of, {name: 90 for name in FACTOR_NAMES}, 20, {}), config)
        self.weak = assess_stock(AnalysisInput("2330", self.position.as_of, {name: 25 for name in FACTOR_NAMES}, 80, {}), config)

    def test_profit_metrics(self) -> None:
        metrics = calculate_metrics(self.position)
        self.assertEqual(metrics.unrealized_profit, 20000)
        self.assertEqual(metrics.unrealized_profit_pct, 20)

    def test_add_advice_has_trigger_reasons(self) -> None:
        advice = advise_position(self.position, self.strong, 20, self.rules)
        self.assertEqual(advice.action, "加碼觀察")
        self.assertGreaterEqual(len(advice.triggered_conditions), 2)

    def test_reduce_advice_has_trigger_reasons(self) -> None:
        advice = advise_position(self.position, self.weak, 80, self.rules)
        self.assertEqual(advice.action, "減碼／風險控管")
        self.assertTrue(advice.triggered_conditions)

    def test_persist_position_advice_closes_its_connection(self) -> None:
        # Regression test: persist_position_advice used a bare `with
        # sqlite3.connect(...)`, which on Windows only manages the
        # transaction and never actually closes the connection, leaving a
        # file lock behind -- the same bug class fixed 3x in
        # historical_storage.py. unlink() failing with PermissionError
        # right after a write is the concrete symptom.
        database = Path("test_portfolio_persist.sqlite")
        if database.exists():
            database.unlink()
        advice = advise_position(self.position, self.strong, 20, self.rules)
        persist_position_advice(database, advice, datetime.now(timezone.utc), "v1")
        database.unlink()  # must not raise PermissionError


if __name__ == "__main__":
    unittest.main()
