"""Tests for multi-lot buys/sells and realized/unrealized profit accounting."""

import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path

from transaction_ledger import Transaction, add_transaction, calculate_holdings, owner_summary, set_current_price


class TransactionLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_ledger.sqlite")
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE IF EXISTS transactions")
            connection.execute("DROP TABLE IF EXISTS current_prices")
        self.time = datetime(2026, 7, 22, 13, 30, tzinfo=timezone.utc)

    def test_multiple_buy_sell_lots_calculate_profit(self) -> None:
        add_transaction(self.database, Transaction(None, "Will", "2330", self.time, "BUY", 100, 100, 10))
        add_transaction(self.database, Transaction(None, "Will", "2330", self.time, "BUY", 100, 120, 10))
        add_transaction(self.database, Transaction(None, "Will", "2330", self.time, "SELL", 50, 150, 10))
        set_current_price(self.database, "2330", 130, self.time)
        holding = calculate_holdings(self.database)[0]
        self.assertEqual(holding.shares, 150)
        self.assertAlmostEqual(holding.realized_profit, 1985, places=2)
        self.assertAlmostEqual(holding.unrealized_profit, 2985, places=2)

    def test_sell_beyond_available_is_rejected(self) -> None:
        add_transaction(self.database, Transaction(None, "Will", "2330", self.time, "BUY", 10, 100))
        with self.assertRaises(ValueError):
            add_transaction(self.database, Transaction(None, "Will", "2330", self.time, "SELL", 11, 100))

    def test_owner_summary_separates_people(self) -> None:
        add_transaction(self.database, Transaction(None, "Will", "2330", self.time, "BUY", 10, 100))
        add_transaction(self.database, Transaction(None, "Family", "2330", self.time, "BUY", 10, 200))
        set_current_price(self.database, "2330", 150, self.time)
        self.assertEqual(owner_summary(self.database, "Will")["unrealized_profit"], 500)
        self.assertEqual(owner_summary(self.database, "Family")["unrealized_profit"], -500)


if __name__ == "__main__":
    unittest.main()
