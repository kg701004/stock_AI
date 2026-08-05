"""Tests for capped, diversified allocation suggestions."""

import unittest
from pathlib import Path

from portfolio_allocation import build_allocation_plan, load_allocation_rules
from portfolio_risk import SecurityMetadata
from transaction_ledger import LedgerHolding


class PortfolioAllocationTests(unittest.TestCase):
    def test_concentrated_owner_gets_reduce_suggestion(self) -> None:
        holdings = [
            LedgerHolding("Will", "2330", 1000, 100, 120, 100000, 120000, 20000, 20, 0, 0),
            LedgerHolding("Will", "2317", 100, 100, 100, 10000, 10000, 0, 0, 0, 0),
        ]
        metadata = {"2330": SecurityMetadata("2330", "半導體", 1.1), "2317": SecurityMetadata("2317", "電子", 1.0)}
        plan = build_allocation_plan("Will", holdings, {"2330": 80, "2317": 70}, metadata, load_allocation_rules(Path("config/allocation_rules.json")))
        tsmc = next(item for item in plan.suggestions if item.symbol == "2330")
        self.assertEqual(tsmc.action, "減碼")
        self.assertLessEqual(tsmc.target_weight_pct, 20)

    def test_plan_uses_invested_value_not_unverified_cash(self) -> None:
        holding = LedgerHolding("Will", "2330", 100, 100, 100, 10000, 10000, 0, 0, 0, 0)
        plan = build_allocation_plan("Will", [holding], {"2330": 80}, {"2330": SecurityMetadata("2330", "半導體", 1)}, load_allocation_rules(Path("config/allocation_rules.json")))
        self.assertEqual(plan.portfolio_value, 10000)

    def test_watchlist_candidate_can_receive_new_position_suggestion(self) -> None:
        holding = LedgerHolding("Will", "2330", 100, 100, 100, 10000, 10000, 0, 0, 0, 0)
        metadata = {"2330": SecurityMetadata("2330", "半導體", 1), "2603": SecurityMetadata("2603", "航運", 1)}
        plan = build_allocation_plan("Will", [holding], {"2330": 60, "2603": 90}, metadata, load_allocation_rules(Path("config/allocation_rules.json")), candidate_symbols=["2603"])
        candidate = next(item for item in plan.suggestions if item.symbol == "2603")
        self.assertEqual(candidate.action, "建立部位")
        self.assertGreater(candidate.target_weight_pct, 0)

    def test_cash_balance_is_added_to_total_portfolio_value(self) -> None:
        holding = LedgerHolding("Will", "2330", 100, 100, 100, 10000, 10000, 0, 0, 0, 0)
        plan = build_allocation_plan("Will", [holding], {"2330": 80}, {"2330": SecurityMetadata("2330", "半導體", 1)}, load_allocation_rules(Path("config/allocation_rules.json")), cash_balance=5000)
        self.assertEqual(plan.portfolio_value, 15000)
        self.assertEqual(plan.cash_balance, 5000)
        self.assertAlmostEqual(plan.cash_weight_pct, 5000 / 15000 * 100, places=2)

    def test_minimum_cash_reserve_caps_investable_weight(self) -> None:
        holding = LedgerHolding("Will", "2330", 1000, 100, 100, 100000, 100000, 0, 0, 0, 0)
        rules = load_allocation_rules(Path("config/allocation_rules.json"))
        self.assertEqual(rules["minimum_cash_reserve_pct"], 15)
        plan = build_allocation_plan("Will", [holding], {"2330": 80}, {"2330": SecurityMetadata("2330", "半導體", 1)}, rules)
        tsmc = next(item for item in plan.suggestions if item.symbol == "2330")
        self.assertLessEqual(tsmc.target_weight_pct, 85.0)

    def test_negative_cash_balance_is_rejected(self) -> None:
        holding = LedgerHolding("Will", "2330", 100, 100, 100, 10000, 10000, 0, 0, 0, 0)
        with self.assertRaises(ValueError):
            build_allocation_plan("Will", [holding], {"2330": 80}, {"2330": SecurityMetadata("2330", "半導體", 1)}, load_allocation_rules(Path("config/allocation_rules.json")), cash_balance=-100)

    def test_low_cash_below_reserve_target_triggers_warning(self) -> None:
        holding = LedgerHolding("Will", "2330", 1000, 100, 100, 100000, 100000, 0, 0, 0, 0)
        plan = build_allocation_plan("Will", [holding], {"2330": 80}, {"2330": SecurityMetadata("2330", "半導體", 1)}, load_allocation_rules(Path("config/allocation_rules.json")), cash_balance=0)
        self.assertTrue(any("現金部位" in warning for warning in plan.warnings))
