"""Tests for auto-deriving the `technical` factor score from local daily-bar history."""

import sqlite3
import unittest
from pathlib import Path

from technical_factor import liquidity_factor_score, liquidity_score_from_avg_daily_value, technical_factor_score
from historical_storage import average_daily_trading_value


def _seed_daily_bars(database: Path, symbol: str, count: int) -> None:
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
        price = 100.0
        for day in range(count):
            price += 0.5  # steady uptrend so the technical signal has a clear bullish MA lineup
            date = f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}"
            connection.execute(
                "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST', ?, 'chk')",
                (symbol, date, int((price - 1) * 1_000_000), int((price + 1) * 1_000_000),
                 int((price - 2) * 1_000_000), int(price * 1_000_000), 1_000_000 + day * 1000, f"{date}T13:30:00+08:00"),
            )
        connection.commit()
    finally:
        connection.close()


class TechnicalFactorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_technical_factor.sqlite")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def test_missing_database_returns_none_with_explanation(self) -> None:
        score, note = technical_factor_score(self.database, "2330")
        self.assertIsNone(score)
        self.assertIn("尚無本機歷史資料", note)

    def test_insufficient_history_returns_none_with_explanation(self) -> None:
        _seed_daily_bars(self.database, "2330", 10)
        score, note = technical_factor_score(self.database, "2330")
        self.assertIsNone(score)
        self.assertIn("少於自動計算所需", note)

    def test_enough_history_produces_a_real_score(self) -> None:
        _seed_daily_bars(self.database, "2330", 65)
        score, note = technical_factor_score(self.database, "2330")
        self.assertIsNotNone(score)
        self.assertTrue(0 <= score <= 100)
        self.assertTrue(note)

    def test_liquidity_score_from_avg_daily_value_follows_higher_is_more_liquid_convention(self) -> None:
        self.assertEqual(liquidity_score_from_avg_daily_value(3_000_000), 20.0)  # thin-liquidity anchor
        self.assertEqual(liquidity_score_from_avg_daily_value(500_000_000), 90.0)  # blue-chip anchor
        self.assertGreater(liquidity_score_from_avg_daily_value(50_000_000), liquidity_score_from_avg_daily_value(5_000_000))
        self.assertEqual(liquidity_score_from_avg_daily_value(0), 0.0)

    def test_liquidity_factor_score_uses_real_recent_trading_value(self) -> None:
        _seed_daily_bars(self.database, "2330", 25)
        avg_value = average_daily_trading_value(self.database, "2330", window=20)
        self.assertIsNotNone(avg_value)
        score, note = liquidity_factor_score(self.database, "2330")
        self.assertEqual(score, liquidity_score_from_avg_daily_value(avg_value))
        self.assertIn("平均成交金額", note)

    def test_liquidity_factor_score_is_none_without_local_history(self) -> None:
        score, note = liquidity_factor_score(self.database, "2330")
        self.assertIsNone(score)
        self.assertIn("尚無本機歷史資料", note)


if __name__ == "__main__":
    unittest.main()
