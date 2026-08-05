"""Tests for editable ownerless watchlist persistence."""

import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path

from watchlist_repository import add_item, delete_item, list_items


class WatchlistRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_watchlist.sqlite")
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE IF EXISTS watchlist_items")

    def test_add_and_delete_watchlist_item(self) -> None:
        item_id = add_item(self.database, "2330", "台積電", 900, 950, 860, datetime(2026, 7, 22, tzinfo=timezone.utc))
        self.assertEqual(list_items(self.database)[0].symbol, "2330")
        delete_item(self.database, item_id)
        self.assertEqual(list_items(self.database), [])
