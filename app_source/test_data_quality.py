"""Tests for the quality gate that decides whether imported daily bars are
accepted or rejected -- previously had zero direct test coverage despite
being the mechanism that answers "what if the updated data has a problem?".

Some rejection branches in validate_daily_bars() (invalid OHLC, negative
volume) are defense-in-depth: DailyBar's own __post_init__ already refuses to
construct such an instance, so a *real* DailyBar can never carry bad OHLC or
negative volume. Those branches are exercised here with plain duck-typed
stand-ins (not real DailyBar instances) since validate_daily_bars() only
reads attributes and never checks isinstance -- this documents that the
check is a deliberate second line of defense, not dead code.
"""

import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone

from data_quality import validate_daily_bars
from historical_storage import DailyBar


def _bar(symbol="2330", trading_date=date(2026, 6, 1), open_price=10.0, high_price=11.0, low_price=9.0, close_price=10.5, volume=1000, source="TEST"):
    return DailyBar(symbol, trading_date, open_price, high_price, low_price, close_price, volume, source, datetime(2026, 6, 1, tzinfo=timezone.utc))


@dataclass
class _FakeBar:
    """Duck-typed stand-in with no constructor validation, for exercising
    validate_daily_bars() branches a real DailyBar can never reach."""
    symbol: str
    trading_date: date
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    source: str


class DataQualityTests(unittest.TestCase):
    def test_clean_bars_are_accepted(self) -> None:
        report = validate_daily_bars([_bar(), _bar(symbol="2317")])
        self.assertTrue(report.accepted)
        self.assertEqual(report.errors, ())

    def test_empty_batch_is_rejected(self) -> None:
        report = validate_daily_bars([])
        self.assertFalse(report.accepted)
        self.assertIn("no daily bars", report.errors[0])

    def test_duplicate_symbol_date_source_is_rejected(self) -> None:
        """Two rows for the same (symbol, date, source) would otherwise let
        INSERT OR REPLACE silently keep only one while archive_and_import
        still reports the original, now-inflated row count as a success."""
        report = validate_daily_bars([_bar(volume=1000), _bar(volume=2000)])
        self.assertFalse(report.accepted)
        self.assertTrue(any("duplicate" in error for error in report.errors))

    def test_real_dailybar_can_never_carry_invalid_ohlc_or_negative_volume(self) -> None:
        """Confirms the *primary* line of defense: DailyBar's own
        constructor, which run_manual_update / archive_and_import always go
        through, refuses bad data before data_quality even runs."""
        with self.assertRaises(ValueError):
            DailyBar("2330", date(2026, 6, 1), open_price=10.0, high_price=5.0, low_price=9.0, close_price=10.5, volume=1000, source="TEST", published_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
        with self.assertRaises(ValueError):
            DailyBar("2330", date(2026, 6, 1), open_price=10.0, high_price=11.0, low_price=9.0, close_price=10.5, volume=-5, source="TEST", published_at=datetime(2026, 6, 1, tzinfo=timezone.utc))

    def test_invalid_ohlc_is_rejected_as_defense_in_depth(self) -> None:
        bad = _FakeBar("2330", date(2026, 6, 1), open_price=10.0, high_price=5.0, low_price=9.0, close_price=10.5, volume=1000, source="TEST")
        report = validate_daily_bars([bad])
        self.assertFalse(report.accepted)
        self.assertTrue(any("invalid OHLC" in error for error in report.errors))

    def test_negative_volume_is_rejected_as_defense_in_depth(self) -> None:
        bad = _FakeBar("2330", date(2026, 6, 1), open_price=10.0, high_price=11.0, low_price=9.0, close_price=10.5, volume=-5, source="TEST")
        report = validate_daily_bars([bad])
        self.assertFalse(report.accepted)
        self.assertTrue(any("negative volume" in error for error in report.errors))

    def test_unexpected_date_is_a_warning_not_a_rejection(self) -> None:
        report = validate_daily_bars([_bar(trading_date=date(2026, 6, 1))], expected_date=date(2026, 6, 2))
        self.assertTrue(report.accepted)
        self.assertTrue(any("unexpected date" in warning for warning in report.warnings))


if __name__ == "__main__":
    unittest.main()
