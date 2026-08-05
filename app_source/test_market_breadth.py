"""Tests for market breadth (advance/decline) and sector relative strength,
both computed from data the app already collects locally -- no new fetch."""

import sqlite3
import unittest
from pathlib import Path

from market_breadth import (
    compute_market_breadth, compute_sector_relative_return,
    market_breadth_factor_score, market_breadth_score_from_snapshot,
    sector_rotation_factor_score, sector_rotation_score_from_returns,
)


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


def _seed_securities(database: Path, rows: list[tuple[str, str]]) -> None:
    """rows: (symbol, sector)."""
    connection = sqlite3.connect(database)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS securities (
                symbol TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT NOT NULL,
                sector TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
            )
        """)
        for symbol, sector in rows:
            connection.execute("INSERT INTO securities VALUES (?, ?, 'TWSE', ?, '2026-01-01', '2026-01-01')", (symbol, symbol, sector))
        connection.commit()
    finally:
        connection.close()


class MarketBreadthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_market_breadth.sqlite")
        self.database.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.database.unlink(missing_ok=True)

    def test_computes_real_advance_decline_across_two_dates(self) -> None:
        _seed_daily_bars(self.database, [
            ("2330", "2026-07-30", 100_000_000), ("2330", "2026-07-31", 110_000_000),  # up
            ("2317", "2026-07-30", 100_000_000), ("2317", "2026-07-31", 90_000_000),  # down
            ("2454", "2026-07-30", 100_000_000), ("2454", "2026-07-31", 100_000_000),  # unchanged
        ])
        snapshot = compute_market_breadth(self.database)
        self.assertEqual((snapshot.advancing, snapshot.declining, snapshot.unchanged), (1, 1, 1))
        self.assertEqual(snapshot.trading_date, "2026-07-31")

    def test_score_direction_more_advancers_scores_higher(self) -> None:
        bullish = compute_market_breadth_from_counts(80, 20)
        bearish = compute_market_breadth_from_counts(20, 80)
        self.assertGreater(market_breadth_score_from_snapshot(bullish), market_breadth_score_from_snapshot(bearish))

    def test_factor_score_is_none_with_insufficient_history(self) -> None:
        score, note = market_breadth_factor_score(self.database)
        self.assertIsNone(score)
        self.assertIn("不足兩個交易日", note)

    def test_factor_score_end_to_end_with_real_seeded_data(self) -> None:
        _seed_daily_bars(self.database, [
            ("2330", "2026-07-30", 100_000_000), ("2330", "2026-07-31", 110_000_000),
            ("2317", "2026-07-30", 100_000_000), ("2317", "2026-07-31", 105_000_000),
            ("2454", "2026-07-30", 100_000_000), ("2454", "2026-07-31", 95_000_000),
        ])
        score, note = market_breadth_factor_score(self.database)
        self.assertIsNotNone(score)
        self.assertIn("上漲 2", note)
        self.assertIn("下跌 1", note)


class SectorRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_sector_rotation.sqlite")
        self.database.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.database.unlink(missing_ok=True)

    def test_computes_real_sector_vs_market_average_return(self) -> None:
        _seed_securities(self.database, [("2330", "半導體業"), ("2454", "半導體業"), ("2317", "電子零組件業")])
        _seed_daily_bars(self.database, [
            ("2330", "2026-07-30", 100_000_000), ("2330", "2026-07-31", 110_000_000),  # +10%
            ("2454", "2026-07-30", 100_000_000), ("2454", "2026-07-31", 106_000_000),  # +6%
            ("2317", "2026-07-30", 100_000_000), ("2317", "2026-07-31", 94_000_000),  # -6%
        ])
        result = compute_sector_relative_return(self.database, "2330")
        self.assertIsNotNone(result)
        sector_return, market_return, sector = result
        self.assertEqual(sector, "半導體業")
        self.assertAlmostEqual(sector_return, 8.0, places=1)  # mean(+10%, +6%)
        self.assertAlmostEqual(market_return, (10 + 6 - 6) / 3, places=1)  # mean of all three

    def test_score_direction_outperformance_scores_higher(self) -> None:
        leading = sector_rotation_score_from_returns(sector_return_pct=5.0, market_return_pct=1.0)
        lagging = sector_rotation_score_from_returns(sector_return_pct=-3.0, market_return_pct=1.0)
        self.assertGreater(leading, lagging)

    def test_factor_score_is_none_without_catalogued_sector(self) -> None:
        score, note = sector_rotation_factor_score(self.database, "9999")
        self.assertIsNone(score)
        self.assertIn("無此股票的產業分類", note)

    def test_factor_score_end_to_end_with_real_seeded_data(self) -> None:
        _seed_securities(self.database, [("2330", "半導體業"), ("2317", "電子零組件業")])
        _seed_daily_bars(self.database, [
            ("2330", "2026-07-30", 100_000_000), ("2330", "2026-07-31", 110_000_000),
            ("2317", "2026-07-30", 100_000_000), ("2317", "2026-07-31", 95_000_000),
        ])
        score, note = sector_rotation_factor_score(self.database, "2330")
        self.assertIsNotNone(score)
        self.assertIn("半導體業", note)


def compute_market_breadth_from_counts(advancing: int, declining: int):
    from market_breadth import BreadthSnapshot
    return BreadthSnapshot("2026-07-31", "2026-07-30", advancing, declining, 0)


if __name__ == "__main__":
    unittest.main()
