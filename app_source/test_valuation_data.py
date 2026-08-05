"""Tests for the TWSE free valuation (P/E, dividend yield, P/B) adapter."""

import unittest
from datetime import date
from pathlib import Path

from valuation_data import (
    ValuationSnapshot, latest_valuation, parse_valuation_records,
    store_valuation_snapshots, valuation_factor_score, valuation_score_from_snapshot,
)


class ValuationParseTests(unittest.TestCase):
    def test_parses_real_shaped_record_and_roc_date(self) -> None:
        records = [{"Date": "1150731", "Code": "2330", "Name": "台積電", "PEratio": "32.60", "DividendYield": "0.91", "PBratio": "10.67"}]
        snapshots = parse_valuation_records(records)
        self.assertEqual(len(snapshots), 1)
        snapshot = snapshots[0]
        self.assertEqual(snapshot.symbol, "2330")
        self.assertEqual(snapshot.trading_date, date(2026, 7, 31))
        self.assertAlmostEqual(snapshot.pe_ratio, 32.60)
        self.assertAlmostEqual(snapshot.dividend_yield_pct, 0.91)
        self.assertAlmostEqual(snapshot.pb_ratio, 10.67)

    def test_skips_non_four_digit_codes_and_missing_values(self) -> None:
        records = [
            {"Date": "1150731", "Code": "00679B", "PEratio": "10", "DividendYield": "1", "PBratio": "1"},  # ETF/bond code, not 4-digit
            {"Date": "1150731", "Code": "2317", "PEratio": "--", "DividendYield": "", "PBratio": "-"},
        ]
        snapshots = parse_valuation_records(records)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].symbol, "2317")
        self.assertIsNone(snapshots[0].pe_ratio)
        self.assertIsNone(snapshots[0].dividend_yield_pct)
        self.assertIsNone(snapshots[0].pb_ratio)

    def test_malformed_date_is_skipped_not_crashed(self) -> None:
        records = [{"Date": "bad", "Code": "2330", "PEratio": "10", "DividendYield": "1", "PBratio": "1"}]
        self.assertEqual(parse_valuation_records(records), [])


class ValuationScoreTests(unittest.TestCase):
    def test_lower_pe_scores_higher(self) -> None:
        cheap = ValuationSnapshot("2330", date(2026, 7, 31), pe_ratio=10.0, dividend_yield_pct=None, pb_ratio=None)
        expensive = ValuationSnapshot("2330", date(2026, 7, 31), pe_ratio=40.0, dividend_yield_pct=None, pb_ratio=None)
        self.assertGreater(valuation_score_from_snapshot(cheap), valuation_score_from_snapshot(expensive))

    def test_higher_yield_scores_higher(self) -> None:
        low_yield = ValuationSnapshot("2330", date(2026, 7, 31), pe_ratio=None, dividend_yield_pct=0.0, pb_ratio=None)
        high_yield = ValuationSnapshot("2330", date(2026, 7, 31), pe_ratio=None, dividend_yield_pct=6.0, pb_ratio=None)
        self.assertGreater(valuation_score_from_snapshot(high_yield), valuation_score_from_snapshot(low_yield))

    def test_lower_pb_scores_higher(self) -> None:
        cheap = ValuationSnapshot("2330", date(2026, 7, 31), pe_ratio=None, dividend_yield_pct=None, pb_ratio=1.0)
        expensive = ValuationSnapshot("2330", date(2026, 7, 31), pe_ratio=None, dividend_yield_pct=None, pb_ratio=5.0)
        self.assertGreater(valuation_score_from_snapshot(cheap), valuation_score_from_snapshot(expensive))

    def test_loss_making_pe_is_excluded_not_guessed(self) -> None:
        snapshot = ValuationSnapshot("2330", date(2026, 7, 31), pe_ratio=-5.0, dividend_yield_pct=2.0, pb_ratio=None)
        # only the yield subscore should count; a negative PE must not be treated as "very cheap"
        score = valuation_score_from_snapshot(snapshot)
        self.assertIsNotNone(score)
        yield_only = valuation_score_from_snapshot(ValuationSnapshot("2330", date(2026, 7, 31), pe_ratio=None, dividend_yield_pct=2.0, pb_ratio=None))
        self.assertEqual(score, yield_only)

    def test_all_missing_returns_none(self) -> None:
        snapshot = ValuationSnapshot("2330", date(2026, 7, 31), pe_ratio=None, dividend_yield_pct=None, pb_ratio=None)
        self.assertIsNone(valuation_score_from_snapshot(snapshot))


class ValuationStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_valuation_data.sqlite")
        self.database.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.database.unlink(missing_ok=True)

    def test_store_and_latest_round_trip(self) -> None:
        snapshots = [ValuationSnapshot("2330", date(2026, 7, 30), 30.0, 1.0, 10.0), ValuationSnapshot("2330", date(2026, 7, 31), 32.6, 0.91, 10.67)]
        count = store_valuation_snapshots(self.database, snapshots)
        self.assertEqual(count, 2)
        latest = latest_valuation(self.database, "2330")
        self.assertEqual(latest.trading_date, date(2026, 7, 31))  # the newer of the two, not just the last inserted
        self.assertAlmostEqual(latest.pe_ratio, 32.6)

    def test_latest_valuation_is_none_for_unknown_symbol_or_missing_database(self) -> None:
        self.assertIsNone(latest_valuation(self.database, "2330"))
        self.assertIsNone(latest_valuation(Path("data/test_valuation_never_created.sqlite"), "2330"))

    def test_valuation_factor_score_end_to_end(self) -> None:
        store_valuation_snapshots(self.database, [ValuationSnapshot("2330", date(2026, 7, 31), 32.6, 0.91, 10.67)])
        score, note = valuation_factor_score(self.database, "2330")
        self.assertEqual(score, valuation_score_from_snapshot(ValuationSnapshot("2330", date(2026, 7, 31), 32.6, 0.91, 10.67)))
        self.assertIn("本益比 32.60", note)

    def test_valuation_factor_score_is_none_without_local_data(self) -> None:
        score, note = valuation_factor_score(self.database, "2330")
        self.assertIsNone(score)
        self.assertIn("尚無本機評價資料", note)


if __name__ == "__main__":
    unittest.main()
