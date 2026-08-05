"""Tests for the SQLite-backed factor scores that replaced the 2-stock sample CSV."""

import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from external_data_importers import import_vix, parse_fred_vix_csv
from factor_score_store import (
    DEFAULT_MANUAL_FACTOR_SCORE, DEFAULT_RISK_SCORE, SEEDED_NOTE_KEY,
    MANUAL_FACTOR_NAMES, load_all_current_assessments, load_symbol_factor_scores,
    save_factor_scores, seed_default_factor_scores,
)
from fundamentals_data import RevenueSnapshot, store_revenue_snapshots
from historical_storage import DailyBar, archive_and_import
from twse_daily_importer import write_normalized_csv


class FactorScoreStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decision_database = Path("test_factor_scores_decision.sqlite")
        self.history_database = Path("test_factor_scores_history.sqlite")
        for database in (self.decision_database, self.history_database):
            if database.exists():
                database.unlink()

    def tearDown(self) -> None:
        for database in (self.decision_database, self.history_database):
            if database.exists():
                database.unlink()

    def _factors(self, value: float = 60.0) -> dict[str, float]:
        return {name: value for name in MANUAL_FACTOR_NAMES}

    def test_naive_as_of_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            save_factor_scores(self.decision_database, "2454", datetime(2026, 7, 24), self._factors(), 40, {})

    def test_incomplete_factors_are_rejected(self) -> None:
        incomplete = self._factors()
        del incomplete[MANUAL_FACTOR_NAMES[0]]
        with self.assertRaises(ValueError):
            save_factor_scores(self.decision_database, "2454", datetime(2026, 7, 24, tzinfo=timezone.utc), incomplete, 40, {})

    def test_out_of_range_score_is_rejected(self) -> None:
        bad = self._factors(); bad[MANUAL_FACTOR_NAMES[0]] = 150
        with self.assertRaises(ValueError):
            save_factor_scores(self.decision_database, "2454", datetime(2026, 7, 24, tzinfo=timezone.utc), bad, 40, {})

    def test_save_and_load_round_trips(self) -> None:
        save_factor_scores(self.decision_database, "2454", datetime(2026, 7, 24, tzinfo=timezone.utc), self._factors(72), 30, {"fundamentals": "good quarter"})
        loaded = load_symbol_factor_scores(self.decision_database, "2454")
        self.assertIsNotNone(loaded)
        manual_values, risk_score, notes = loaded
        self.assertEqual(manual_values["fundamentals"], 72)
        self.assertEqual(risk_score, 30)
        self.assertEqual(notes["fundamentals"], "good quarter")

    def test_missing_symbol_returns_none(self) -> None:
        self.assertIsNone(load_symbol_factor_scores(self.decision_database, "9999"))

    def test_saving_again_updates_rather_than_duplicates_the_same_as_of(self) -> None:
        as_of = datetime(2026, 7, 24, tzinfo=timezone.utc)
        save_factor_scores(self.decision_database, "2454", as_of, self._factors(60), 30, {})
        save_factor_scores(self.decision_database, "2454", as_of, self._factors(80), 30, {})
        manual_values, _, _ = load_symbol_factor_scores(self.decision_database, "2454")
        self.assertEqual(manual_values[MANUAL_FACTOR_NAMES[0]], 80)

    def test_seed_default_factor_scores_uses_neutral_defaults_without_local_history(self) -> None:
        seeded = seed_default_factor_scores(self.decision_database, self.history_database, "2454", datetime(2026, 7, 24, tzinfo=timezone.utc))
        self.assertTrue(seeded)
        manual_values, risk_score, notes = load_symbol_factor_scores(self.decision_database, "2454")
        self.assertEqual(risk_score, DEFAULT_RISK_SCORE)
        self.assertTrue(all(value == DEFAULT_MANUAL_FACTOR_SCORE for value in manual_values.values()))
        self.assertIn(SEEDED_NOTE_KEY, notes)

    def test_seed_default_factor_scores_uses_real_vix_and_volume_when_available(self) -> None:
        # global_risk_factor_score (sentiment_fear.py) needs at least 6 days
        # of local VIX history to compute a real percentile/5-day-change
        # score; fewer than that falls back to a neutral default.
        import_vix(self.history_database, parse_fred_vix_csv(
            b"observation_date,VIXCLS\n2026-07-21,16.0\n2026-07-22,16.5\n2026-07-23,17.0\n"
            b"2026-07-24,17.5\n2026-07-27,18.0\n2026-07-28,18.21\n"
        ))
        bars = [DailyBar("6182", date(2026, 7, 28), 84.0, 85.0, 83.0, 84.7, 20_000_000, "TEST", datetime(2026, 7, 28, tzinfo=timezone.utc))]
        csv_path = Path("data/test_factor_score_seed.csv")
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, self.history_database, Path("data/test_factor_score_seed_archive"))

        seeded = seed_default_factor_scores(self.decision_database, self.history_database, "6182", datetime(2026, 7, 30, tzinfo=timezone.utc))
        self.assertTrue(seeded)
        manual_values, _, notes = load_symbol_factor_scores(self.decision_database, "6182")
        self.assertNotEqual(manual_values["global_risk"], DEFAULT_MANUAL_FACTOR_SCORE)  # real VIX-derived
        self.assertNotEqual(manual_values["liquidity"], DEFAULT_MANUAL_FACTOR_SCORE)  # real volume-derived
        self.assertEqual(manual_values["fundamentals"], DEFAULT_MANUAL_FACTOR_SCORE)  # no revenue data seeded in this test
        self.assertIn(SEEDED_NOTE_KEY, notes)

    def test_seed_default_factor_scores_uses_real_revenue_when_available(self) -> None:
        store_revenue_snapshots(self.history_database, [RevenueSnapshot("6182", "11506", 25.0)])
        seeded = seed_default_factor_scores(self.decision_database, self.history_database, "6182", datetime(2026, 7, 30, tzinfo=timezone.utc))
        self.assertTrue(seeded)
        manual_values, _, _ = load_symbol_factor_scores(self.decision_database, "6182")
        self.assertNotEqual(manual_values["fundamentals"], DEFAULT_MANUAL_FACTOR_SCORE)

    def test_seed_default_factor_scores_never_overwrites_an_existing_score(self) -> None:
        save_factor_scores(self.decision_database, "2454", datetime(2026, 7, 24, tzinfo=timezone.utc), self._factors(72), 30, {"fundamentals": "a human really looked at this"})
        seeded = seed_default_factor_scores(self.decision_database, self.history_database, "2454", datetime(2026, 7, 25, tzinfo=timezone.utc))
        self.assertFalse(seeded)
        manual_values, _, notes = load_symbol_factor_scores(self.decision_database, "2454")
        self.assertEqual(manual_values["fundamentals"], 72)
        self.assertNotIn(SEEDED_NOTE_KEY, notes)

    def test_load_all_current_assessments_merges_technical_and_falls_back_when_insufficient_history(self) -> None:
        save_factor_scores(self.decision_database, "2454", datetime(2026, 7, 24, tzinfo=timezone.utc), self._factors(65), 25, {})
        assessments = load_all_current_assessments(self.decision_database, self.history_database)
        self.assertIn("2454", assessments)
        input_row = assessments["2454"]
        self.assertEqual(input_row.factors["technical"], 50.0)  # no history_database yet -> neutral fallback
        self.assertIn("尚無本機歷史資料", input_row.notes["technical"])
        self.assertEqual(input_row.factors["fundamentals"], 65)
        self.assertEqual(input_row.risk_score, 25)


if __name__ == "__main__":
    unittest.main()
