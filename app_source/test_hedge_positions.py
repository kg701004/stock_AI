"""Tests for the persisted per-owner futures hedge position."""

import unittest
from datetime import datetime
from pathlib import Path

from hedge_positions import load_position, save_position, clear_position


class HedgePositionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_hedge_positions.sqlite")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def test_missing_position_returns_none(self) -> None:
        self.assertIsNone(load_position(self.database, "Will", "MTX"))

    def test_save_and_load_round_trip(self) -> None:
        now = datetime.now().astimezone()
        save_position(self.database, "Will", "MTX", -2.0, 21600, now, "放空避險")
        position = load_position(self.database, "Will", "MTX")
        self.assertEqual(position.contracts, -2.0)
        self.assertEqual(position.index_points, 21600)
        self.assertEqual(position.note, "放空避險")

    def test_saving_again_updates_existing_row(self) -> None:
        now = datetime.now().astimezone()
        save_position(self.database, "Will", "MTX", -2.0, 21600, now)
        save_position(self.database, "Will", "MTX", -3.5, 21700, now)
        position = load_position(self.database, "Will", "MTX")
        self.assertEqual(position.contracts, -3.5)
        self.assertEqual(position.index_points, 21700)

    def test_contracts_are_independent_per_contract_type(self) -> None:
        now = datetime.now().astimezone()
        save_position(self.database, "Will", "MTX", -2.0, 21600, now)
        save_position(self.database, "Will", "TX", 1.0, 21600, now)
        self.assertEqual(load_position(self.database, "Will", "MTX").contracts, -2.0)
        self.assertEqual(load_position(self.database, "Will", "TX").contracts, 1.0)

    def test_clear_position_removes_row(self) -> None:
        now = datetime.now().astimezone()
        save_position(self.database, "Will", "MTX", -2.0, 21600, now)
        clear_position(self.database, "Will", "MTX")
        self.assertIsNone(load_position(self.database, "Will", "MTX"))

    def test_rejects_unknown_contract(self) -> None:
        with self.assertRaises(ValueError):
            save_position(self.database, "Will", "XL", 1.0, 21600, datetime.now().astimezone())

    def test_rejects_blank_owner(self) -> None:
        with self.assertRaises(ValueError):
            save_position(self.database, "  ", "MTX", 1.0, 21600, datetime.now().astimezone())


if __name__ == "__main__":
    unittest.main()
