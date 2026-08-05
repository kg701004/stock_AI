"""Tests for short-term reversal signal."""

import sqlite3
import unittest
from pathlib import Path

from technical_layers import Bar
from short_term_reversal import short_term_reversal_signal, calculate_short_term_reversal_for_symbol


def _seed_daily_bars(database: Path, symbol: str, prices: list[float]) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol TEXT NOT NULL, trading_date TEXT NOT NULL,
                open_micros INTEGER NOT NULL, high_micros INTEGER NOT NULL,
                low_micros INTEGER NOT NULL, close_micros INTEGER NOT NULL,
                volume INTEGER NOT NULL, source TEXT NOT NULL, published_at TEXT NOT NULL,
                import_checksum TEXT NOT NULL,
                PRIMARY KEY(symbol, trading_date, source)
            )
        """)
        for idx, price in enumerate(prices):
            date = f"2026-01-{1 + idx:02d}"
            connection.execute(
                "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST', ?, 'chk')",
                (symbol, date, int((price - 1) * 1_000_000), int((price + 1) * 1_000_000),
                 int((price - 2) * 1_000_000), int(price * 1_000_000), 1_000_000, f"{date}T13:30:00+08:00"),
            )
        connection.commit()
    finally:
        connection.close()


class ShortTermReversalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_short_term_reversal.sqlite")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def test_insufficient_data_returns_false(self) -> None:
        # lookback = 5, needs at least 6 bars (lookback + 1)
        bars = [Bar(100.0, 101.0, 99.0, 1000) for _ in range(5)]
        self.assertFalse(short_term_reversal_signal(bars, lookback=5))

    def test_normal_trigger_reversal(self) -> None:
        # Price goes from 100.0 to 90.0, which is a 10% drop. Since 10% >= 8%, it should trigger.
        bars = [
            Bar(100.0, 101.0, 99.0, 1000), # 5 days ago (relative to last)
            Bar(99.0, 100.0, 98.0, 1000),
            Bar(98.0, 99.0, 97.0, 1000),
            Bar(97.0, 98.0, 96.0, 1000),
            Bar(95.0, 96.0, 94.0, 1000),
            Bar(90.0, 91.0, 89.0, 1000),  # last bar
        ]
        self.assertTrue(short_term_reversal_signal(bars, lookback=5, drop_pct=8.0))

    def test_exactly_equal_to_threshold_triggers(self) -> None:
        # Price goes from 100.0 to 92.0, which is exactly an 8% drop. Should trigger.
        bars = [
            Bar(100.0, 101.0, 99.0, 1000), # 5 days ago
            Bar(99.0, 100.0, 98.0, 1000),
            Bar(98.0, 99.0, 97.0, 1000),
            Bar(97.0, 98.0, 96.0, 1000),
            Bar(95.0, 96.0, 94.0, 1000),
            Bar(92.0, 93.0, 91.0, 1000),  # last bar
        ]
        self.assertTrue(short_term_reversal_signal(bars, lookback=5, drop_pct=8.0))

    def test_drop_less_than_threshold_does_not_trigger(self) -> None:
        # Price goes from 100.0 to 93.0, which is a 7% drop. Since 7% < 8%, it should not trigger.
        bars = [
            Bar(100.0, 101.0, 99.0, 1000), # 5 days ago
            Bar(99.0, 100.0, 98.0, 1000),
            Bar(98.0, 99.0, 97.0, 1000),
            Bar(97.0, 98.0, 96.0, 1000),
            Bar(95.0, 96.0, 94.0, 1000),
            Bar(93.0, 94.0, 92.0, 1000),  # last bar
        ]
        self.assertFalse(short_term_reversal_signal(bars, lookback=5, drop_pct=8.0))

    def test_calculate_short_term_reversal_from_database(self) -> None:
        # Test helper function that reads from SQLite db
        prices = [100.0, 99.0, 98.0, 97.0, 95.0, 90.0]
        _seed_daily_bars(self.database, "2330", prices)
        triggered = calculate_short_term_reversal_for_symbol(self.database, "2330", lookback=5, drop_pct=8.0)
        self.assertTrue(triggered)

    def test_calculate_short_term_reversal_database_missing_returns_false(self) -> None:
        triggered = calculate_short_term_reversal_for_symbol(self.database, "2330", lookback=5, drop_pct=8.0)
        self.assertFalse(triggered)


if __name__ == "__main__":
    unittest.main()
