"""Tests for the 散戶槓桿 (retail_leverage) factor score."""

import sqlite3
import unittest
from pathlib import Path

from retail_leverage import retail_leverage_factor_score


def _seed_daily_bars(database: Path, symbol: str, volumes: list[int]) -> None:
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
        for index, volume in enumerate(volumes):
            trading_date = f"2026-07-{index + 1:02d}"
            connection.execute(
                "INSERT INTO daily_bars VALUES (?, ?, 100, 100, 100, 100, ?, 'TEST', ?, 'chk')",
                (symbol, trading_date, volume, f"{trading_date}T13:30:00+08:00"),
            )
        connection.commit()
    finally:
        connection.close()


def _seed_margin_balance(database: Path, symbol: str, net_changes: list[tuple[int, int]]) -> None:
    """net_changes: list of (margin_net_change_shares, short_net_change_shares)."""
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS margin_balance_history (
                trading_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                margin_net_change_shares INTEGER NOT NULL,
                short_net_change_shares INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY(trading_date, symbol)
            )
        """)
        for index, (margin_change, short_change) in enumerate(net_changes):
            trading_date = f"2026-07-{index + 1:02d}"
            connection.execute(
                "INSERT INTO margin_balance_history VALUES (?, ?, ?, ?, '2026-01-01T00:00:00')",
                (trading_date, symbol, margin_change, short_change),
            )
        connection.commit()
    finally:
        connection.close()


class RetailLeverageFactorScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_retail_leverage_temp.sqlite")
        self.database.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.database.unlink(missing_ok=True)

    def test_missing_database_returns_none(self) -> None:
        score, note = retail_leverage_factor_score(self.database, "2330")
        self.assertIsNone(score)
        self.assertIn("尚無歷史資料庫", note)

    def test_no_leverage_history_returns_none(self) -> None:
        self.database.touch()
        score, note = retail_leverage_factor_score(self.database, "2330")
        self.assertIsNone(score)
        self.assertIn("尚無此股票的融資融券餘額資料", note)

    def test_leverage_history_without_volume_history_returns_none(self) -> None:
        _seed_margin_balance(self.database, "2330", [(500_000, 0)])
        _seed_daily_bars(self.database, "2330", [])  # creates the table with no rows for this symbol
        score, note = retail_leverage_factor_score(self.database, "2330")
        self.assertIsNone(score)
        self.assertIn("尚無此股票的日線成交量資料", note)

    def test_rising_margin_balance_raises_score_above_neutral(self) -> None:
        _seed_margin_balance(self.database, "2330", [(500_000, 0), (500_000, 0)])
        _seed_daily_bars(self.database, "2330", [2_000_000] * 10)
        score, note = retail_leverage_factor_score(self.database, "2330")
        # cumulative_net = (500000-0)+(500000-0) = 1,000,000; average_volume=2,000,000; ratio=0.5; score=50+0.5*25=62.5
        self.assertEqual(score, 62.5)
        self.assertIn("淨增加", note)
        self.assertIn("+0.50", note)

    def test_rising_short_balance_lowers_score_below_neutral(self) -> None:
        _seed_margin_balance(self.database, "2330", [(0, 1_000_000)])
        _seed_daily_bars(self.database, "2330", [2_000_000] * 5)
        score, note = retail_leverage_factor_score(self.database, "2330")
        # cumulative_net = 0-1,000,000 = -1,000,000; ratio=-0.5; score=50-0.5*25=37.5
        self.assertEqual(score, 37.5)
        self.assertIn("淨減少", note)

    def test_extreme_ratio_is_clamped_to_0_100_range(self) -> None:
        _seed_margin_balance(self.database, "2330", [(100_000_000, 0)])
        _seed_daily_bars(self.database, "2330", [1_000])
        score, _note = retail_leverage_factor_score(self.database, "2330")
        self.assertEqual(score, 100.0)

    def test_only_uses_this_symbols_own_data(self) -> None:
        _seed_margin_balance(self.database, "2330", [(500_000, 0)])
        _seed_daily_bars(self.database, "2330", [2_000_000])
        score, note = retail_leverage_factor_score(self.database, "6182")
        self.assertIsNone(score)
        self.assertIn("尚無此股票的融資融券餘額資料", note)


if __name__ == "__main__":
    unittest.main()
