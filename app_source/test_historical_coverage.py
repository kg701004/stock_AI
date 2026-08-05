import sqlite3
import unittest
from datetime import date
from pathlib import Path

from historical_coverage import check_coverage, check_universe


class HistoricalCoverageTests(unittest.TestCase):
    def _database(self, database: Path, symbol: str, years: range, bars_per_year: int) -> Path:
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE daily_bars (symbol TEXT, trading_date TEXT)")
            for year in years:
                for day in range(1, bars_per_year + 1):
                    connection.execute("INSERT INTO daily_bars VALUES (?, ?)", (symbol, date(year, 1, 1).fromordinal(date(year, 1, 1).toordinal() + day - 1).isoformat()))
            connection.commit()
        finally:
            connection.close()
        return database

    def _database_path(self, name: str) -> Path:
        root = Path("data/test_history_coverage")
        root.mkdir(parents=True, exist_ok=True)
        database = root / f"{name}.sqlite"
        database.unlink(missing_ok=True)
        return database

    def test_full_ten_calendar_years_are_ready(self) -> None:
        database = self._database(self._database_path("ready"), "2330", range(2016, 2026), 200)
        report = check_coverage(database, "2330", 10, date(2026, 7, 22))
        self.assertTrue(report.ready_for_backtest)
        self.assertEqual(report.missing_years, ())
        self.assertEqual(report.total_bars, 2000)

    def test_partial_year_is_explicitly_blocked(self) -> None:
        database = self._database(self._database_path("partial"), "2330", range(2016, 2026), 200)
        connection = sqlite3.connect(database)
        try:
            connection.execute("DELETE FROM daily_bars WHERE trading_date LIKE '2022-%'")
            connection.commit()
        finally:
            connection.close()
        report = check_coverage(database, "2330", 10, date(2026, 7, 22))
        self.assertFalse(report.ready_for_backtest)
        self.assertIn(2022, report.missing_years)
        self.assertIn("2022", report.message)

    def test_recent_listing_is_distinguished_from_a_real_data_gap(self) -> None:
        """A stock that only IPO'd 3 years ago will never reach 10 full
        calendar years -- that must be reported as "too young", not as a
        data-quality problem the user should try to re-backfill away."""
        database = self._database(self._database_path("young"), "6182", range(2023, 2026), 200)
        report = check_coverage(database, "6182", 10, date(2026, 7, 22))
        self.assertFalse(report.ready_for_backtest)
        self.assertEqual(report.missing_years, tuple(range(2016, 2023)))
        self.assertIn("上市", report.message)
        self.assertNotIn("建議重新匯入或回補", report.message)
        self.assertIn("3／10", report.message)

    def test_real_gap_after_listing_is_still_flagged_for_reimport(self) -> None:
        database = self._database(self._database_path("young_with_gap"), "6182", range(2023, 2026), 200)
        connection = sqlite3.connect(database)
        try:
            connection.execute("DELETE FROM daily_bars WHERE trading_date LIKE '2024-%'")
            connection.commit()
        finally:
            connection.close()
        report = check_coverage(database, "6182", 10, date(2026, 7, 22))
        self.assertFalse(report.ready_for_backtest)
        self.assertIn("缺口", report.message)
        self.assertIn("2024", report.message)
        self.assertIn("上市", report.message)  # still notes the pre-IPO years separately

    def test_missing_database_and_invalid_symbol_are_safe(self) -> None:
        database = self._database_path("missing")
        report = check_coverage(database, "2330", 10, date(2026, 7, 22))
        self.assertFalse(report.ready_for_backtest)
        self.assertEqual(len(check_universe(database, ["2330", "2317"], 10, date(2026, 7, 22))), 2)
        with self.assertRaises(ValueError):
            check_coverage(database, "ABC")


if __name__ == "__main__":
    unittest.main()
