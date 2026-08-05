"""Tests for the recent-window daily close series that feeds the price chart."""

import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from historical_storage import DailyBar, archive_and_import
from price_chart_data import load_recent_closes
from twse_daily_importer import write_normalized_csv


class PriceChartDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_price_chart_data.sqlite")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def _import(self, bars: list[DailyBar], name: str) -> None:
        csv_path = Path(f"data/test_price_chart_{name}.csv")
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, self.database, Path("data/test_price_chart_archive"))

    def test_missing_database_returns_empty(self) -> None:
        self.assertEqual(load_recent_closes(Path("does_not_exist.sqlite"), "2330", 30), [])

    def test_unknown_symbol_returns_empty(self) -> None:
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        self._import([DailyBar("2330", date(2026, 7, 24), 100, 105, 95, 102, 1000, "TEST", now)], "seed")
        self.assertEqual(load_recent_closes(self.database, "9999", 30), [])

    def test_returns_real_closes_within_the_window(self) -> None:
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        bars = [DailyBar("2330", date(2026, 7, 1 + i), 100 + i, 105 + i, 95 + i, 100 + i, 1000, "TEST", now) for i in range(10)]
        self._import(bars, "series")
        closes = load_recent_closes(self.database, "2330", 5)
        self.assertEqual([c.close for c in closes], [105.0, 106.0, 107.0, 108.0, 109.0])
        self.assertEqual(closes[0].trading_date, date(2026, 7, 6))
        self.assertEqual(closes[-1].trading_date, date(2026, 7, 10))

    def test_window_is_relative_to_the_symbols_own_latest_date_not_todays_real_date(self) -> None:
        """A post-market tool: "recent" should mean recent relative to the
        stock's own last archived trading day, not the wall-clock date --
        otherwise a chart requested on a day before today's snapshot has
        landed would look nearly empty even though real recent history exists."""
        now = datetime(2020, 1, 1, tzinfo=timezone.utc)  # long before "today" in any real sense
        bars = [DailyBar("2330", date(2020, 1, 1 + i), 100 + i, 110 + i, 90 + i, 100 + i, 1000, "TEST", now) for i in range(5)]
        self._import(bars, "old")
        closes = load_recent_closes(self.database, "2330", 3)
        self.assertEqual(len(closes), 3)
        self.assertEqual(closes[-1].trading_date, date(2020, 1, 5))

    def test_ex_dividend_gap_is_removed_consistent_with_technical_factor(self) -> None:
        from dividend_adjustment import store_events
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        self._import([
            DailyBar("2330", date(2026, 5, 29), 1000, 1005, 995, 1000, 1000, "TEST", now),
            DailyBar("2330", date(2026, 6, 1), 950, 955, 945, 950, 1000, "TEST", now),  # ex-date
        ], "exdiv")
        store_events(self.database, [("2330", date(2026, 6, 1), 1000.0, 950.0)])
        closes = load_recent_closes(self.database, "2330", 365)
        self.assertAlmostEqual(closes[0].close, 950.0, places=2)  # back-adjusted, no artificial gap
        self.assertEqual(closes[1].close, 950.0)


if __name__ == "__main__":
    unittest.main()
