"""Tests for the offline proof of concept."""

import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from no_api_prototype import TAIPEI, Tick, aggregate_minutes, generate_mock_ticks, persist, rsi, scan, sma


class OfflinePrototypeTests(unittest.TestCase):
    def test_mock_ticks_are_deterministic(self) -> None:
        start = datetime(2026, 7, 22, 9, tzinfo=TAIPEI)
        self.assertEqual(generate_mock_ticks("2330", start, 5, 9), generate_mock_ticks("2330", start, 5, 9))

    def test_aggregation_sorts_out_of_order_ticks(self) -> None:
        start = datetime(2026, 7, 22, 9, tzinfo=TAIPEI)
        ticks = [Tick("2330", start + timedelta(seconds=40), start, 102, 3), Tick("2330", start + timedelta(seconds=10), start, 100, 2)]
        candle = aggregate_minutes(ticks)[0]
        self.assertEqual((candle.open, candle.high, candle.low, candle.close, candle.volume), (100, 102, 100, 102, 5))

    def test_indicators_do_not_fabricate_early_values(self) -> None:
        self.assertEqual(sma([1, 2, 3], 3), [None, None, 2])
        self.assertEqual(rsi([1] * 13), [None] * 13)

    def test_scan_and_sqlite_persistence(self) -> None:
        start = datetime(2026, 7, 22, 9, tzinfo=TAIPEI)
        candles = aggregate_minutes(generate_mock_ticks("2330", start, 30))
        signal = scan(candles)
        # Keep the temporary database in the workspace: the desktop sandbox
        # intentionally prevents this Python process from writing to system Temp.
        database = Path(__file__).with_name("test_offline.sqlite")
        persist(database, candles, signal)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM candles").fetchone()[0], 30)
            self.assertGreaterEqual(connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
