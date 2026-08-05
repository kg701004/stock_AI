"""End-to-end user workflows covering GUI-equivalent ledger actions."""

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from transaction_ledger import Transaction, add_transaction, calculate_holdings, delete_transaction, owner_summary, set_current_price


class UserWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_user_workflow.sqlite")
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE IF EXISTS transactions")
            connection.execute("DROP TABLE IF EXISTS current_prices")
        self.start = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)

    def test_user_can_record_lots_value_holdings_and_close_position(self) -> None:
        buy_one = add_transaction(self.database, Transaction(None, "Will", "2330", self.start, "BUY", 1000, 800, 100, "first lot"))
        add_transaction(self.database, Transaction(None, "Will", "2330", self.start + timedelta(days=10), "BUY", 500, 900, 100, "add lot"))
        add_transaction(self.database, Transaction(None, "Will", "2330", self.start + timedelta(days=20), "SELL", 600, 950, 150, "partial sell"))
        set_current_price(self.database, "2330", 920, self.start + timedelta(days=21))
        open_holding = calculate_holdings(self.database)[0]
        self.assertEqual(open_holding.shares, 900)
        self.assertGreater(open_holding.realized_profit, 0)
        self.assertGreater(open_holding.unrealized_profit or 0, 0)
        add_transaction(self.database, Transaction(None, "Will", "2330", self.start + timedelta(days=30), "SELL", 900, 910, 100, "close"))
        self.assertEqual(calculate_holdings(self.database), [])
        self.assertGreater(owner_summary(self.database, "Will")["realized_profit"], 0)
        self.assertGreater(owner_summary(self.database, "Will")["fees"], 0)
        self.assertIsInstance(buy_one, int)

    def test_user_cannot_delete_buy_that_would_invalidate_later_sale(self) -> None:
        buy_id = add_transaction(self.database, Transaction(None, "Will", "2330", self.start, "BUY", 100, 100))
        add_transaction(self.database, Transaction(None, "Will", "2330", self.start + timedelta(days=1), "SELL", 100, 110))
        with self.assertRaises(ValueError):
            delete_transaction(self.database, buy_id)
        self.assertEqual(owner_summary(self.database, "Will")["realized_profit"], 1000)

    def test_backdated_sale_is_rejected(self) -> None:
        add_transaction(self.database, Transaction(None, "Will", "2330", self.start + timedelta(days=1), "BUY", 100, 100))
        with self.assertRaises(ValueError):
            add_transaction(self.database, Transaction(None, "Will", "2330", self.start, "SELL", 10, 100))
