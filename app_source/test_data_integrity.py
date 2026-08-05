"""Tests for post-download integrity verification of locally archived daily bars."""

import sqlite3
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from data_integrity import scan_ohlc_sanity, scan_trading_day_gaps, verify_data_integrity
from historical_storage import DailyBar, archive_and_import
from twse_daily_importer import write_normalized_csv


class DataIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_data_integrity.sqlite")
        self.archive_dir = Path("data/test_data_integrity_archive")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def _import(self, bars: list[DailyBar], name: str) -> None:
        csv_path = Path(f"data/test_data_integrity_{name}.csv")
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, self.database, self.archive_dir)

    def _insert_raw_bar(self, symbol: str, trading_date: str, open_: float, high: float, low: float, close: float, volume: int) -> None:
        """Insert directly via SQL, bypassing DailyBar's own OHLC validation --
        simulates a malformed row already sitting in the database (e.g. from
        before that validation existed, or external corruption), which is
        exactly the scenario scan_ohlc_sanity exists to catch."""
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
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
            connection.execute(
                "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST', '2026-07-24T00:00:00+08:00', 'chk')",
                (symbol, trading_date, int(open_ * 1_000_000), int(high * 1_000_000),
                 int(low * 1_000_000), int(close * 1_000_000), volume),
            )
            connection.commit()
        finally:
            connection.close()

    def test_fresh_database_reports_no_problems(self) -> None:
        report = verify_data_integrity(self.database)
        self.assertTrue(report.clean)
        self.assertEqual(report.total_bars_checked, 0)

    def test_scan_ohlc_sanity_is_silent_for_well_formed_bars(self) -> None:
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        self._import([DailyBar("2330", date(2026, 7, 24), 100, 105, 95, 102, 1000, "TEST", now)], "ok")
        self.assertEqual(scan_ohlc_sanity(self.database), [])

    def test_scan_ohlc_sanity_flags_high_below_low(self) -> None:
        self._insert_raw_bar("2330", "2026-07-24", 100, 90, 95, 92, 1000)  # high < open/close
        violations = scan_ohlc_sanity(self.database)
        self.assertEqual(len(violations), 1)
        self.assertIn("2330", violations[0])

    def test_scan_ohlc_sanity_flags_close_outside_high_low_range(self) -> None:
        self._insert_raw_bar("2330", "2026-07-24", 100, 105, 95, 999, 1000)  # close way above high
        self.assertEqual(len(scan_ohlc_sanity(self.database)), 1)

    def test_scan_ohlc_sanity_flags_negative_volume(self) -> None:
        self._insert_raw_bar("2330", "2026-07-24", 100, 105, 95, 102, -1)
        self.assertEqual(len(scan_ohlc_sanity(self.database)), 1)

    def test_scan_ohlc_sanity_respects_symbol_filter(self) -> None:
        self._insert_raw_bar("2330", "2026-07-24", 100, 90, 95, 92, 1000)  # bad
        self._insert_raw_bar("6182", "2026-07-24", 100, 105, 95, 102, 1000)  # fine
        self.assertEqual(scan_ohlc_sanity(self.database, symbols=["6182"]), [])
        self.assertEqual(len(scan_ohlc_sanity(self.database, symbols=["2330"])), 1)

    def test_scan_trading_day_gaps_is_silent_when_symbols_share_the_same_dates(self) -> None:
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        self._import([
            DailyBar("2330", date(2026, 7, 22), 100, 105, 95, 102, 1000, "TEST", now),
            DailyBar("2330", date(2026, 7, 23), 100, 105, 95, 102, 1000, "TEST", now),
            DailyBar("6182", date(2026, 7, 22), 50, 55, 45, 52, 1000, "TEST", now),
            DailyBar("6182", date(2026, 7, 23), 50, 55, 45, 52, 1000, "TEST", now),
        ], "aligned")
        self.assertEqual(scan_trading_day_gaps(self.database), [])

    def test_scan_trading_day_gaps_flags_a_symbol_missing_a_date_others_have(self) -> None:
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        self._import([
            DailyBar("2330", date(2026, 7, 22), 100, 105, 95, 102, 1000, "TEST", now),
            DailyBar("2330", date(2026, 7, 23), 100, 105, 95, 102, 1000, "TEST", now),
            DailyBar("2330", date(2026, 7, 24), 100, 105, 95, 102, 1000, "TEST", now),
            DailyBar("6182", date(2026, 7, 22), 50, 55, 45, 52, 1000, "TEST", now),
            # 6182 is missing 2026-07-23, which 2330 has -- and 6182 resumes
            # on 2026-07-24, so 07-23 is squarely inside 6182's own range too.
            DailyBar("6182", date(2026, 7, 24), 50, 55, 45, 52, 1000, "TEST", now),
        ], "gap")
        gaps = scan_trading_day_gaps(self.database)
        self.assertEqual(gaps, [("6182", 1)])

    def test_scan_trading_day_gaps_does_not_flag_dates_before_a_symbols_own_first_date(self) -> None:
        """A recently-listed stock's earlier "missing" dates are not a data
        gap -- it simply didn't exist yet."""
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        self._import([
            DailyBar("2330", date(2020, 1, 2), 100, 105, 95, 102, 1000, "TEST", now),
            DailyBar("2330", date(2026, 7, 24), 100, 105, 95, 102, 1000, "TEST", now),
            DailyBar("6182", date(2026, 7, 24), 50, 55, 45, 52, 1000, "TEST", now),  # newly listed
        ], "recent_listing")
        self.assertEqual(scan_trading_day_gaps(self.database, symbols=["6182"]), [])

    def test_verify_data_integrity_combines_all_three_checks(self) -> None:
        self._insert_raw_bar("2330", "2026-07-22", 100, 90, 95, 92, 1000)  # bad OHLC
        self._insert_raw_bar("6182", "2026-07-23", 50, 55, 45, 52, 1000)  # fine
        report = verify_data_integrity(self.database)
        self.assertFalse(report.clean)
        self.assertEqual(len(report.ohlc_violations), 1)
        self.assertEqual(report.total_bars_checked, 2)

    def test_verify_data_integrity_reports_archive_errors(self) -> None:
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        self._import([DailyBar("2330", date(2026, 7, 24), 100, 105, 95, 102, 1000, "TEST", now)], "to_corrupt")
        report_before = verify_data_integrity(self.database)
        self.assertEqual(report_before.archive_errors, ())

        for archive_file in self.archive_dir.rglob("*.gz"):
            archive_file.unlink()
        report_after = verify_data_integrity(self.database)
        self.assertEqual(len(report_after.archive_errors), 1)


if __name__ == "__main__":
    unittest.main()
