"""Tests for the persisted short-stock position ledger."""

import unittest
from datetime import datetime
from pathlib import Path

from short_positions import close_position, list_positions, open_position


class ShortPositionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_short_positions.sqlite")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def test_open_position_is_listed_and_open(self) -> None:
        now = datetime.now().astimezone()
        open_position(self.database, "Will", "2330", 1000, 900, now, "跌破月線")
        positions = list_positions(self.database, "Will")
        self.assertEqual(len(positions), 1)
        self.assertTrue(positions[0].is_open)
        self.assertEqual(positions[0].note, "跌破月線")

    def test_unrealized_profit_when_price_falls(self) -> None:
        now = datetime.now().astimezone()
        open_position(self.database, "Will", "2330", 1000, 900, now)
        position = list_positions(self.database, "Will")[0]
        self.assertEqual(position.unrealized_profit(850), 50_000)

    def test_unrealized_loss_when_price_rises(self) -> None:
        now = datetime.now().astimezone()
        open_position(self.database, "Will", "2330", 1000, 900, now)
        position = list_positions(self.database, "Will")[0]
        self.assertEqual(position.unrealized_profit(950), -50_000)

    def test_close_position_records_realized_profit(self) -> None:
        now = datetime.now().astimezone()
        position_id = open_position(self.database, "Will", "2330", 1000, 900, now)
        close_position(self.database, position_id, 800, now)
        position = list_positions(self.database, "Will")[0]
        self.assertFalse(position.is_open)
        self.assertEqual(position.realized_profit(), 100_000)

    def test_open_only_filter_excludes_closed_positions(self) -> None:
        now = datetime.now().astimezone()
        position_id = open_position(self.database, "Will", "2330", 1000, 900, now)
        open_position(self.database, "Will", "2317", 500, 100, now)
        close_position(self.database, position_id, 800, now)
        open_positions = list_positions(self.database, "Will", open_only=True)
        self.assertEqual(len(open_positions), 1)
        self.assertEqual(open_positions[0].symbol, "2317")

    def test_cannot_close_already_closed_position(self) -> None:
        now = datetime.now().astimezone()
        position_id = open_position(self.database, "Will", "2330", 1000, 900, now)
        close_position(self.database, position_id, 800, now)
        with self.assertRaises(ValueError):
            close_position(self.database, position_id, 700, now)

    def test_rejects_invalid_symbol(self) -> None:
        with self.assertRaises(ValueError):
            open_position(self.database, "Will", "TSMC", 1000, 900, datetime.now().astimezone())

    def test_rejects_nonpositive_shares_or_price(self) -> None:
        now = datetime.now().astimezone()
        with self.assertRaises(ValueError):
            open_position(self.database, "Will", "2330", 0, 900, now)
        with self.assertRaises(ValueError):
            open_position(self.database, "Will", "2330", 1000, 0, now)


if __name__ == "__main__":
    unittest.main()
