"""Tests for offline public-data market-context analysis."""

import sqlite3
import unittest
from pathlib import Path

from market_context import MarketSnapshot, assess_market, market_context_factor_score


def _seed_daily_bars(database: Path, rows: list[tuple[str, str, int]]) -> None:
    """rows: (symbol, trading_date, close_micros)."""
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
        for symbol, trading_date, close_micros in rows:
            connection.execute(
                "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST', ?, 'chk')",
                (symbol, trading_date, close_micros, close_micros, close_micros, close_micros, 1000, f"{trading_date}T13:30:00+08:00"),
            )
        connection.commit()
    finally:
        connection.close()


def _seed_market_indices(database: Path, rows: list[tuple[str, str, float]]) -> None:
    """rows: (trading_date, market, close_value)."""
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS market_index_history (
                trading_date TEXT NOT NULL,
                market TEXT NOT NULL,
                close_value REAL NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY(trading_date, market)
            );
        """)
        for trading_date, market, close_value in rows:
            connection.execute(
                "INSERT OR REPLACE INTO market_index_history VALUES (?, ?, ?, '2026-01-01T00:00:00')",
                (trading_date, market, close_value),
            )
        connection.commit()
    finally:
        connection.close()


class MarketContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_market_context_temp.sqlite")
        self.database.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.database.unlink(missing_ok=True)

    def test_positive_breadth_produces_bullish_context(self) -> None:
        result = assess_market(MarketSnapshot(700, 300, 80, 10, 700, 1000, 1.2, 1.1))
        self.assertEqual(result.regime, "bullish")
        self.assertGreaterEqual(result.score, 65)

    def test_combined_risks_produce_bearish_context(self) -> None:
        result = assess_market(MarketSnapshot(200, 800, 10, 80, 200, 1000, -2, -1.8, -2.1, 30, 1.4))
        self.assertEqual(result.regime, "bearish")
        self.assertEqual(result.risk_level, "high")

    def test_invalid_universe_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MarketSnapshot(1, 1, 0, 0, 0, 0, 0, 0)

    def test_market_context_factor_score_insufficient_data(self) -> None:
        # Non-existent DB
        score, note = market_context_factor_score(self.database)
        self.assertIsNone(score)
        self.assertIn("指數資料不足", note)

        # DB exists but empty
        self.database.touch()
        score, note = market_context_factor_score(self.database)
        self.assertIsNone(score)
        self.assertIn("指數資料不足", note)

    def test_market_context_factor_score_sufficient_data(self) -> None:
        # Seed indices (2 days each)
        _seed_market_indices(self.database, [
            ("2026-07-30", "TWSE", 16000.0),
            ("2026-07-31", "TWSE", 16160.0), # +1.0%
            ("2026-07-30", "TPEx", 200.0),
            ("2026-07-31", "TPEx", 202.0), # +1.0%
        ])
        # Seed daily bars for breadth (2 days)
        _seed_daily_bars(self.database, [
            ("2330", "2026-07-30", 100_000_000), ("2330", "2026-07-31", 110_000_000),  # up
            ("2317", "2026-07-30", 100_000_000), ("2317", "2026-07-31", 105_000_000),  # up
            ("2454", "2026-07-30", 100_000_000), ("2454", "2026-07-31", 100_000_000),  # unchanged
        ])

        score, note = market_context_factor_score(self.database)
        self.assertIsNotNone(score)
        self.assertEqual(score, 60.0)  # base 50 + 10 (listed and OTC rose together >= 1%) = 60
        self.assertIsInstance(score, float)
        self.assertIn("大盤情境", note)
        self.assertIn("（註：創新高/創新低/站上20日均線家數目前無真實資料來源，本次以0計算）", note)


if __name__ == "__main__":
    unittest.main()
