"""Tests for the TWSE free monthly-revenue adapter (fundamentals factor)."""

import unittest
from pathlib import Path

from fundamentals_data import (
    RevenueSnapshot, fundamentals_factor_score, fundamentals_score_from_snapshot,
    latest_revenue, parse_revenue_records, store_revenue_snapshots,
)


class RevenueParseTests(unittest.TestCase):
    def test_parses_real_shaped_record(self) -> None:
        records = [{"公司代號": "2330", "公司名稱": "台積電", "資料年月": "11506", "營業收入-去年同月增減(%)": "67.87"}]
        snapshots = parse_revenue_records(records)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].symbol, "2330")
        self.assertEqual(snapshots[0].year_month, "11506")
        self.assertAlmostEqual(snapshots[0].year_over_year_growth_pct, 67.87)

    def test_skips_non_four_digit_codes_and_handles_missing_growth(self) -> None:
        records = [
            {"公司代號": "ABCDE", "資料年月": "11506", "營業收入-去年同月增減(%)": "10"},
            {"公司代號": "2317", "資料年月": "11506", "營業收入-去年同月增減(%)": "--"},
        ]
        snapshots = parse_revenue_records(records)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].symbol, "2317")
        self.assertIsNone(snapshots[0].year_over_year_growth_pct)


class FundamentalsScoreTests(unittest.TestCase):
    def test_higher_growth_scores_higher(self) -> None:
        declining = fundamentals_score_from_snapshot(RevenueSnapshot("2330", "11506", -20.0))
        growing = fundamentals_score_from_snapshot(RevenueSnapshot("2330", "11506", 30.0))
        self.assertGreater(growing, declining)

    def test_missing_growth_returns_none(self) -> None:
        self.assertIsNone(fundamentals_score_from_snapshot(RevenueSnapshot("2330", "11506", None)))


class FundamentalsStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_fundamentals_data.sqlite")
        self.database.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.database.unlink(missing_ok=True)

    def test_store_and_latest_round_trip(self) -> None:
        snapshots = [RevenueSnapshot("2330", "11505", 60.0), RevenueSnapshot("2330", "11506", 67.87)]
        count = store_revenue_snapshots(self.database, snapshots)
        self.assertEqual(count, 2)
        latest = latest_revenue(self.database, "2330")
        self.assertEqual(latest.year_month, "11506")  # the newer of the two

    def test_factor_score_end_to_end(self) -> None:
        store_revenue_snapshots(self.database, [RevenueSnapshot("2330", "11506", 67.87)])
        score, note = fundamentals_factor_score(self.database, "2330")
        self.assertEqual(score, fundamentals_score_from_snapshot(RevenueSnapshot("2330", "11506", 67.87)))
        self.assertIn("+67.87%", note)

    def test_factor_score_is_none_without_local_data(self) -> None:
        score, note = fundamentals_factor_score(self.database, "2330")
        self.assertIsNone(score)
        self.assertIn("尚無本機營收資料", note)


if __name__ == "__main__":
    unittest.main()
