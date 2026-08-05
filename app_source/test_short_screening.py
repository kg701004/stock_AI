"""Tests for the independent short-candidate screening module."""

import sqlite3
import unittest
from pathlib import Path

from short_screening import (
    assess_short_candidate,
    financial_deterioration_score,
    margin_trading_signal,
    technical_breakdown_score,
)


def _seed_daily_bars(database: Path, symbol: str, closes: list[float], supports_relative_volume: float = 1.0) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol TEXT NOT NULL, trading_date TEXT NOT NULL,
                open_micros INTEGER NOT NULL, high_micros INTEGER NOT NULL,
                low_micros INTEGER NOT NULL, close_micros INTEGER NOT NULL,
                volume INTEGER NOT NULL, source TEXT NOT NULL, published_at TEXT NOT NULL,
                import_checksum TEXT NOT NULL, PRIMARY KEY(symbol, trading_date, source)
            )
        """)
        for day, close in enumerate(closes):
            volume = int(1_000_000 * (supports_relative_volume if day == len(closes) - 1 else 1.0))
            date = f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}"
            connection.execute(
                "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST', ?, 'chk')",
                (symbol, date, int((close - 1) * 1_000_000), int((close + 1) * 1_000_000),
                 int((close - 2) * 1_000_000), int(close * 1_000_000), volume, f"{date}T13:30:00+08:00"),
            )
        connection.commit()
    finally:
        connection.close()


def _seed_financials(database: Path, symbol: str, periods: list[tuple[int, int, float, float, float, float, float]]) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS mops_financials (
                symbol TEXT NOT NULL, fiscal_year INTEGER NOT NULL, fiscal_quarter INTEGER NOT NULL,
                revenue REAL, eps REAL, gross_margin REAL, operating_margin REAL, roe REAL, debt_ratio REAL,
                source TEXT NOT NULL, imported_at TEXT NOT NULL,
                PRIMARY KEY(symbol,fiscal_year,fiscal_quarter,source)
            )
        """)
        for year, quarter, revenue, gross_margin, operating_margin, roe, debt_ratio in periods:
            connection.execute(
                "INSERT INTO mops_financials VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, 'TEST', '2026-07-24T00:00:00+08:00')",
                (symbol, year, quarter, revenue, gross_margin, operating_margin, roe, debt_ratio),
            )
        connection.commit()
    finally:
        connection.close()


class ShortScreeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history_database = Path("test_short_screen_history.sqlite")
        self.decision_database = Path("test_short_screen_decision.sqlite")
        for database in (self.history_database, self.decision_database):
            if database.exists():
                database.unlink()

    def tearDown(self) -> None:
        for database in (self.history_database, self.decision_database):
            if database.exists():
                database.unlink()

    def test_technical_breakdown_reports_insufficient_history(self) -> None:
        score, notes = technical_breakdown_score(self.history_database, "2454")
        self.assertIsNone(score)
        self.assertIn("尚無本機歷史資料", notes[0])

    def test_technical_breakdown_scores_bearish_alignment_and_confirmed_breakdown(self) -> None:
        # 60 flat days around 100 (support ~98), then a sharp final-day drop that
        # genuinely breaks below the prior 20-day support range, on high volume.
        closes = [100.0] * 60 + [95.0, 90.0, 85.0, 70.0, 60.0]
        _seed_daily_bars(self.history_database, "2454", closes, supports_relative_volume=2.0)
        score, notes = technical_breakdown_score(self.history_database, "2454")
        self.assertIsNotNone(score)
        self.assertGreater(score, 50)
        self.assertTrue(any("空頭排列" in note for note in notes))

    def test_financial_deterioration_reports_insufficient_periods(self) -> None:
        score, notes = financial_deterioration_score(self.decision_database, "2454")
        self.assertIsNone(score)
        self.assertIn("尚無財報資料", notes[0])

    def test_financial_deterioration_flags_worsening_metrics(self) -> None:
        _seed_financials(self.decision_database, "2454", [
            (2025, 4, 1000.0, 0.40, 0.20, 0.15, 0.30),
            (2026, 1, 800.0, 0.35, 0.15, 0.10, 0.40),
        ])
        score, notes = financial_deterioration_score(self.decision_database, "2454")
        self.assertEqual(score, 100.0)
        self.assertEqual(len(notes), 5)

    def test_financial_deterioration_finds_nothing_when_metrics_improve(self) -> None:
        _seed_financials(self.decision_database, "2454", [
            (2025, 4, 800.0, 0.30, 0.10, 0.08, 0.40),
            (2026, 1, 1000.0, 0.40, 0.20, 0.15, 0.30),
        ])
        score, notes = financial_deterioration_score(self.decision_database, "2454")
        self.assertEqual(score, 0.0)

    def test_financial_deterioration_skips_comparison_across_mismatched_sources(self) -> None:
        # A manually uploaded MOPS CSV and the automated TWSE importer are not
        # guaranteed to report revenue in the same unit (e.g. NT$ thousands vs
        # millions) -- comparing across sources must not fabricate a signal.
        _seed_financials(self.decision_database, "2454", [(2025, 4, 1000.0, 0.40, 0.20, 0.15, 0.30)])
        connection = sqlite3.connect(self.decision_database)
        try:
            connection.execute(
                "INSERT INTO mops_financials VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, 'MANUAL_CSV', '2026-07-24T00:00:00+08:00')",
                ("2454", 2026, 1, 800.0, 0.35, 0.15, 0.10, 0.40),
            )
            connection.commit()
        finally:
            connection.close()
        score, notes = financial_deterioration_score(self.decision_database, "2454")
        self.assertIsNone(score)
        self.assertIn("來源不同", notes[0])

    def test_margin_trading_signal_is_honestly_unsupported(self) -> None:
        score, notes = margin_trading_signal("2454")
        self.assertIsNone(score)
        self.assertIn("尚未支援", notes[0])

    def test_assess_short_candidate_combines_all_three(self) -> None:
        result = assess_short_candidate(self.history_database, self.decision_database, "2454")
        self.assertEqual(result.symbol, "2454")
        self.assertIsNone(result.technical_score)
        self.assertIsNone(result.financial_score)
        self.assertIn("尚未支援", result.unsupported_warnings[0])


if __name__ == "__main__":
    unittest.main()
