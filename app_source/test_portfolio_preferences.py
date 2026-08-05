import sqlite3
import unittest
from pathlib import Path

from portfolio_preferences import get_cash_balance, set_cash_balance


class PortfolioPreferencesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_portfolio_preferences.sqlite")
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE IF EXISTS portfolio_preferences")

    def test_cash_balance_is_saved_per_owner(self) -> None:
        set_cash_balance(self.database, "Will", 12345.5)
        self.assertEqual(get_cash_balance(self.database, "Will"), 12345.5)
        self.assertEqual(get_cash_balance(self.database, "Other"), 0)

    def test_negative_cash_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            set_cash_balance(self.database, "Will", -1)
