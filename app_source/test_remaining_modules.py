import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from data_quality import validate_factor_inputs
from decision_journal import JournalEntry, add_entry, list_entries
from walk_forward import ScoreObservation, evaluate
from update_scheduler import due_sources
from weighted_analysis import AnalysisInput

class RemainingModulesTests(unittest.TestCase):
    def test_quality_flags_duplicate_and_stale(self):
        row = AnalysisInput("2330", datetime(2026, 1, 1, tzinfo=timezone.utc), {}, 0, {})
        report = validate_factor_inputs([row, row], datetime(2026, 7, 1, tzinfo=timezone.utc))
        self.assertFalse(report.accepted); self.assertTrue(report.warnings)
    def test_walk_forward_never_uses_same_day_close(self):
        result = evaluate([ScoreObservation("1", "2330", 80, 100), ScoreObservation("2", "2330", 20, 110), ScoreObservation("3", "2330", 80, 90)])
        self.assertEqual(result.trades, 1); self.assertEqual(result.average_return_pct, 10)
    def test_journal_persists_reason(self):
        database = Path("data/test_journal.sqlite")
        add_entry(database, JournalEntry("2330", "觀察", 70, "測試理由", datetime.now(timezone.utc)))
        self.assertEqual(list_entries(database)[0].reason, "測試理由")
    def test_scheduler_only_returns_due_sources(self):
        now = datetime(2026, 7, 22, 16, 10, tzinfo=timezone.utc)
        due = due_sources(now, set())
        self.assertTrue(any(source.startswith("TWSE") for source in due))
        self.assertFalse(any(source.startswith("TPEx") for source in due))
