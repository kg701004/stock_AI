"""Tests for the persisted per-owner broker commission discount."""

import unittest
from pathlib import Path

from broker_discount import DEFAULT_DISCOUNT, load_discount, save_discount


class BrokerDiscountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_broker_discount.sqlite")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def test_missing_owner_defaults_to_full_rate(self) -> None:
        self.assertEqual(load_discount(self.database, "Amy"), DEFAULT_DISCOUNT)

    def test_save_and_load_round_trip(self) -> None:
        save_discount(self.database, "Amy", 0.6)
        self.assertEqual(load_discount(self.database, "Amy"), 0.6)

    def test_owners_are_independent(self) -> None:
        save_discount(self.database, "Amy", 0.6)
        save_discount(self.database, "John", 0.28)
        self.assertEqual(load_discount(self.database, "Amy"), 0.6)
        self.assertEqual(load_discount(self.database, "John"), 0.28)

    def test_rejects_out_of_range_discount(self) -> None:
        with self.assertRaises(ValueError):
            save_discount(self.database, "Amy", 0)
        with self.assertRaises(ValueError):
            save_discount(self.database, "Amy", 1.2)

    def test_rejects_blank_owner(self) -> None:
        with self.assertRaises(ValueError):
            save_discount(self.database, "  ", 0.6)


if __name__ == "__main__":
    unittest.main()
