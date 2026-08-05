"""Tests for ex-dividend/ex-rights back-adjustment of historical daily bars."""

import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from dividend_adjustment import (
    adjust_bars, events_factor_score, events_score_from_days_ahead,
    load_adjustment_factors, next_ex_rights_event, parse_ex_rights_events, store_events,
)
from historical_storage import DailyBar


class DividendAdjustmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_dividend_adjustment.sqlite")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def test_parse_ex_rights_events_converts_roc_date_and_computes_factor_inputs(self) -> None:
        rows = [["115年06月01日", "2330", "台積電", "1000.00", "950.00", "0.5", "息", "1050", "950", "950", "950", "detail"]]
        events = parse_ex_rights_events(rows)
        self.assertEqual(len(events), 1)
        symbol, ex_date, pre_close, reference_price = events[0]
        self.assertEqual(symbol, "2330")
        self.assertEqual(ex_date, date(2026, 6, 1))
        self.assertEqual(pre_close, 1000.0)
        self.assertEqual(reference_price, 950.0)

    def test_parse_ex_rights_events_skips_malformed_rows(self) -> None:
        rows = [["bad"], ["115年06月01日", "XYZ", "not four digits", "100", "95"]]
        self.assertEqual(parse_ex_rights_events(rows), [])

    def test_store_and_load_round_trips_with_computed_factor(self) -> None:
        store_events(self.database, [("2330", date(2026, 6, 1), 1000.0, 950.0)])
        factors = load_adjustment_factors(self.database, "2330")
        self.assertEqual(len(factors), 1)
        ex_date, factor = factors[0]
        self.assertEqual(ex_date, date(2026, 6, 1))
        self.assertAlmostEqual(factor, 0.95)

    def test_adjust_bars_removes_the_artificial_ex_dividend_gap(self) -> None:
        # Before adjustment: flat at 1000 pre-ex-date, mechanical drop to 950 on/after ex-date.
        now = datetime.now(timezone.utc)
        bars = [
            DailyBar("2330", date(2026, 5, 29), 1000, 1005, 995, 1000, 1000, "TEST", now),
            DailyBar("2330", date(2026, 5, 30), 1000, 1005, 995, 1000, 1000, "TEST", now),
            DailyBar("2330", date(2026, 6, 1), 950, 955, 945, 950, 1000, "TEST", now),  # ex-date itself
            DailyBar("2330", date(2026, 6, 2), 950, 955, 945, 950, 1000, "TEST", now),
        ]
        events = [(date(2026, 6, 1), 0.95)]
        adjusted = adjust_bars(bars, events)
        # The two pre-ex-date bars should now read ~950, matching the post-ex-date level -- no more gap.
        self.assertAlmostEqual(adjusted[0].close_price, 950.0, places=2)
        self.assertAlmostEqual(adjusted[1].close_price, 950.0, places=2)
        # On and after the ex-date, prices are untouched.
        self.assertEqual(adjusted[2].close_price, 950.0)
        self.assertEqual(adjusted[3].close_price, 950.0)
        # Volume must never be adjusted.
        self.assertEqual(adjusted[0].volume, 1000)

    def test_adjust_bars_compounds_multiple_events(self) -> None:
        now = datetime.now(timezone.utc)
        bars = [DailyBar("2330", date(2026, 1, 1), 1000, 1000, 1000, 1000, 1000, "TEST", now)]
        events = [(date(2026, 3, 1), 0.9), (date(2026, 6, 1), 0.8)]
        adjusted = adjust_bars(bars, events)
        self.assertAlmostEqual(adjusted[0].close_price, 1000 * 0.9 * 0.8, places=2)

    def test_adjust_bars_is_a_noop_with_no_events(self) -> None:
        now = datetime.now(timezone.utc)
        bars = [DailyBar("2330", date(2026, 1, 1), 1000, 1000, 1000, 1000, 1000, "TEST", now)]
        self.assertEqual(adjust_bars(bars, []), bars)

    def test_events_score_from_days_ahead_is_lowest_when_soonest(self) -> None:
        self.assertEqual(events_score_from_days_ahead(None), 70.0)  # no known event -> calmest
        self.assertEqual(events_score_from_days_ahead(3), 40.0)  # imminent
        self.assertEqual(events_score_from_days_ahead(20), 55.0)  # near-term
        self.assertEqual(events_score_from_days_ahead(90), 70.0)  # far out, same as "none known"

    def test_next_ex_rights_event_finds_the_earliest_upcoming_one(self) -> None:
        store_events(self.database, [
            ("2330", date(2026, 5, 1), 1000.0, 950.0),  # in the past relative to as_of
            ("2330", date(2026, 8, 15), 1000.0, 950.0),
            ("2330", date(2026, 12, 1), 1000.0, 950.0),
        ])
        self.assertEqual(next_ex_rights_event(self.database, "2330", date(2026, 7, 1)), date(2026, 8, 15))
        self.assertIsNone(next_ex_rights_event(self.database, "9999", date(2026, 7, 1)))

    def test_events_factor_score_is_none_when_the_table_has_never_been_populated(self) -> None:
        """A fresh install (or one where the daily update has never run)
        must not confidently claim "no event risk" -- it genuinely doesn't
        know, so it must say so rather than guessing 70."""
        score, note = events_factor_score(self.database, "2330", as_of=date(2026, 7, 1))
        self.assertIsNone(score)
        self.assertIn("尚無除權息事件資料", note)

    def test_events_factor_score_reports_a_real_all_clear_once_the_market_wide_table_has_data(self) -> None:
        # Some OTHER symbol's event is enough to prove the table has really
        # been fetched -- "2330 has nothing upcoming" is then a real fact.
        store_events(self.database, [("9999", date(2026, 9, 1), 1000.0, 950.0)])
        score, note = events_factor_score(self.database, "2330", as_of=date(2026, 7, 1))
        self.assertEqual(score, 70.0)
        self.assertIn("查無近期除權息事件", note)

        store_events(self.database, [("2330", date(2026, 7, 5), 1000.0, 950.0)])
        score, note = events_factor_score(self.database, "2330", as_of=date(2026, 7, 1))
        self.assertEqual(score, 40.0)
        self.assertIn("2026-07-05", note)


if __name__ == "__main__":
    unittest.main()
