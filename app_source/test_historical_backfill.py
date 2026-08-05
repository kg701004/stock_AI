"""Tests for the incremental, resumable TWSE historical backfill."""

import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from database_utils import database_connection
from historical_backfill import estimate_work, fetch_month, parse_month, parse_month_tpex, plan_pending_months, run_backfill
from security_catalog import upsert_from_daily_snapshot


class HistoricalBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history_database = Path("test_backfill_history.sqlite")
        self.imports = Path("data/test_backfill_imports"); self.imports.mkdir(parents=True, exist_ok=True)
        self.archive = Path("data/test_backfill_archive"); self.archive.mkdir(parents=True, exist_ok=True)
        if self.history_database.exists():
            self.history_database.unlink()

    def tearDown(self) -> None:
        if self.history_database.exists():
            self.history_database.unlink()

    def test_parse_month_converts_roc_dates_and_skips_malformed_rows(self) -> None:
        rows = [["115/06/01", "60,942,792", "144,105,259,583", "2,355.00", "2,415.00", "2,350.00", "2,355.00", " 0.00", "136,367", ""], ["bad row"]]
        bars = parse_month("2330", rows, datetime(2026, 7, 24, tzinfo=timezone.utc))
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].trading_date, date(2026, 6, 1))
        self.assertEqual(bars[0].symbol, "2330")

    def test_plan_pending_months_covers_full_range_on_fresh_database(self) -> None:
        pending = plan_pending_months(self.history_database, ["2330"], years=2, as_of=date(2026, 7, 24))
        # 2024 + 2025 (24 months) plus 2026 Jan-Jul (up to and including as_of's month).
        self.assertEqual(len(pending), 2 * 12 + 7)
        self.assertEqual(pending[0], ("2330", 2024, 1))
        self.assertEqual(pending[-1], ("2330", 2026, 7))

    def test_plan_pending_months_detects_a_gap_within_the_current_calendar_year(self) -> None:
        """Regression test for a real production bug: historical_coverage.
        check_coverage's missing_years always excludes the current calendar
        year (it answers "N completed years ready"), so a prior version of
        plan_pending_months that relied on it never flagged gaps within the
        current year at all -- confirmed via a real gap (Jan-Jun 2026 missing
        for 2308/2330/6182) that a completed backfill run silently left
        untouched."""
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        from historical_storage import DailyBar, archive_and_import
        from twse_daily_importer import write_normalized_csv
        # Only December 2025 and July 2026 exist locally -- Jan-Jun 2026 is a real gap.
        bars = [
            DailyBar("2330", date(2025, 12, 31), 100, 101, 99, 100, 1000, "TEST", now),
            DailyBar("2330", date(2026, 7, 22), 100, 101, 99, 100, 1000, "TEST", now),
        ]
        csv_path = Path("data/test_backfill_gap_seed.csv")
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, self.history_database, Path("data/test_backfill_gap_seed_archive"))

        pending = plan_pending_months(self.history_database, ["2330"], years=1, as_of=date(2026, 7, 24))
        pending_months = {(year, month) for _symbol, year, month in pending}
        for month in range(1, 7):
            self.assertIn((2026, month), pending_months, f"2026-{month:02d} should be flagged as a real gap")
        self.assertNotIn((2026, 7), pending_months)  # already has a bar locally
        self.assertNotIn((2025, 12), pending_months)  # already has a bar locally

    def test_plan_pending_months_clamps_to_each_source_real_data_floor(self) -> None:
        """A large `years` request must never enqueue months the source is
        confirmed to reject outright (would fail forever, never marked done):
        TWSE's STOCK_DAY rejects anything before 2010-01 (confirmed live and
        via TWSE's own page text); TPEx's daily endpoint only goes back to
        1994-01 (confirmed via TPEx's own page text). An unregistered symbol
        defaults to the TWSE floor, matching run_backfill's own default."""
        upsert_from_daily_snapshot(self.history_database, [("6182", "合晶")], "TPEx", "2026-07-29T00:00:00")

        twse_pending = plan_pending_months(self.history_database, ["2330"], years=40, as_of=date(2026, 7, 24))
        self.assertEqual(min(year for _symbol, year, _month in twse_pending), 2010)

        tpex_pending = plan_pending_months(self.history_database, ["6182"], years=40, as_of=date(2026, 7, 24))
        self.assertEqual(min(year for _symbol, year, _month in tpex_pending), 1994)

    def test_plan_pending_months_batched_lookup_matches_the_old_per_symbol_result(self) -> None:
        """Regression test for the batched-query optimization (one full-table
        scan instead of one query per symbol -- ~2.5s measured for ~2000
        symbols on the real production database, down to well under 0.2s):
        must return the exact same pending set as before for a mix of
        symbols with existing data, no data, and different markets."""
        from historical_storage import DailyBar, archive_and_import
        from twse_daily_importer import write_normalized_csv
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        upsert_from_daily_snapshot(self.history_database, [("2330", "台積電"), ("6182", "合晶")], "TWSE", "2026-07-29T00:00:00")
        upsert_from_daily_snapshot(self.history_database, [("6182", "合晶")], "TPEx", "2026-07-29T00:00:00")  # corrected market
        bars = [
            DailyBar("2330", date(2026, 6, 1), 100, 105, 95, 102, 1000, "TEST", now),
            DailyBar("6182", date(2026, 5, 1), 50, 55, 45, 52, 1000, "TEST", now),
        ]
        csv_path = Path("data/test_backfill_batched_seed.csv")
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, self.history_database, Path("data/test_backfill_batched_seed_archive"))

        pending = plan_pending_months(self.history_database, ["2330", "6182", "9999"], years=1, as_of=date(2026, 7, 24))
        pending_keys = {(symbol, year, month) for symbol, year, month in pending}
        self.assertNotIn(("2330", 2026, 6), pending_keys)  # already has a bar -- must be skipped
        self.assertNotIn(("6182", 2026, 5), pending_keys)  # already has a bar -- must be skipped
        self.assertIn(("2330", 2026, 7), pending_keys)  # genuinely missing
        self.assertIn(("9999", 2026, 7), pending_keys)  # unregistered symbol, still planned (defaults TWSE floor)

    def test_estimate_work_multiplies_by_throttle(self) -> None:
        count, seconds = estimate_work([("2330", 2024, 1), ("2330", 2024, 2)], throttle_seconds=1.5, max_workers=1)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(seconds, 3.0)

    def test_estimate_work_divides_by_worker_count(self) -> None:
        count, seconds = estimate_work([("2330", 2024, m) for m in range(1, 7)], throttle_seconds=1.5, max_workers=3)
        self.assertEqual(count, 6)
        self.assertAlmostEqual(seconds, 3.0)  # 6 * 1.5 / 3 workers

    def test_run_backfill_persists_progress_and_resumes(self) -> None:
        calls = []
        as_of = date(2026, 7, 24)  # years=1 -> all of 2025 (12) + 2026 Jan-Jul (7) = 19 months

        def fake_fetch(symbol, year, month):
            calls.append((symbol, year, month))
            return [[f"{year - 1911}/{month:02d}/01", "1000", "100000", "10", "11", "9", "10", "0", "5", ""]]

        with patch("historical_backfill.fetch_month", side_effect=fake_fetch), patch("historical_backfill.fetch_ex_rights_events", return_value=[]):
            summary = run_backfill(self.history_database, self.imports, self.archive, ["2330"], years=1, as_of=as_of, max_workers=1)
        self.assertEqual(summary.attempted, 19)
        self.assertEqual(summary.succeeded, 19)
        self.assertEqual(len(calls), 19)

        # Re-running must skip everything already marked done -- no new fetches.
        with patch("historical_backfill.fetch_month", side_effect=fake_fetch) as mocked, patch("historical_backfill.fetch_ex_rights_events", return_value=[]):
            second_summary = run_backfill(self.history_database, self.imports, self.archive, ["2330"], years=1, as_of=as_of, max_workers=1)
        self.assertEqual(second_summary.attempted, 0)
        mocked.assert_not_called()

    def test_run_backfill_records_failures_without_aborting(self) -> None:
        as_of = date(2026, 7, 24)  # years=1 -> 19 months, see test_run_backfill_persists_progress_and_resumes

        def flaky_fetch(symbol, year, month):
            if month == 3:
                raise RuntimeError("simulated network error")
            return [[f"{year - 1911}/{month:02d}/01", "1000", "100000", "10", "11", "9", "10", "0", "5", ""]]

        with patch("historical_backfill.fetch_month", side_effect=flaky_fetch), patch("historical_backfill.fetch_ex_rights_events", return_value=[]):
            summary = run_backfill(self.history_database, self.imports, self.archive, ["2330"], years=1, as_of=as_of, max_workers=1)
        self.assertEqual(summary.attempted, 19)
        self.assertEqual(summary.succeeded, 17)  # month == 3 fails for both 2025 and 2026
        self.assertEqual(len(summary.failed), 2)
        self.assertIn("simulated network error", summary.failed[0])

    def test_run_backfill_stops_early_when_requested(self) -> None:
        def fake_fetch(symbol, year, month):
            return [[f"{year - 1911}/{month:02d}/01", "1000", "100000", "10", "11", "9", "10", "0", "5", ""]]

        with patch("historical_backfill.fetch_month", side_effect=fake_fetch), patch("historical_backfill.fetch_ex_rights_events", return_value=[]):
            summary = run_backfill(self.history_database, self.imports, self.archive, ["2330"], years=1, should_stop=lambda: True, max_workers=1)
        self.assertTrue(summary.stopped_early)
        self.assertEqual(summary.attempted, 0)

    def test_parse_month_tpex_converts_roc_dates_and_scales_board_lot_volume(self) -> None:
        rows = [["113/06/03", "1,889", "73,728", "39.15", "39.30", "38.90", "38.95", "-0.15", "1,410"], ["bad row"]]
        bars = parse_month_tpex("6182", rows, datetime(2026, 7, 24, tzinfo=timezone.utc))
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].trading_date, date(2024, 6, 3))
        self.assertEqual(bars[0].symbol, "6182")
        self.assertEqual(bars[0].volume, 1_889_000)  # 1,889 board lots * 1,000 shares/lot

    def test_run_backfill_routes_known_tpex_symbol_to_tpex_endpoint(self) -> None:
        as_of = date(2026, 7, 24)  # years=1 -> 19 months, see test_run_backfill_persists_progress_and_resumes
        upsert_from_daily_snapshot(self.history_database, [("6182", "合晶")], "TPEx", "2026-07-29T00:00:00")

        def fake_fetch_tpex(symbol, year, month):
            return [[f"{year - 1911}/{month:02d}/01", "1000", "100000", "10", "11", "9", "10", "0", "5"]]

        with patch("historical_backfill.fetch_month_tpex", side_effect=fake_fetch_tpex) as tpex_mock, \
             patch("historical_backfill.fetch_month") as twse_mock, \
             patch("historical_backfill.fetch_ex_rights_events", return_value=[]):
            summary = run_backfill(self.history_database, self.imports, self.archive, ["6182"], years=1, as_of=as_of, max_workers=1)
        self.assertEqual(summary.succeeded, 19)
        self.assertEqual(tpex_mock.call_count, 19)
        twse_mock.assert_not_called()
        with database_connection(self.history_database) as connection:
            markets = {row[0] for row in connection.execute("SELECT market FROM backfill_progress WHERE symbol='6182'")}
        self.assertEqual(markets, {"TPEx"})

    def test_run_backfill_defaults_unknown_symbol_to_twse(self) -> None:
        as_of = date(2026, 7, 24)  # years=1 -> 19 months, see test_run_backfill_persists_progress_and_resumes

        def fake_fetch(symbol, year, month):
            return [[f"{year - 1911}/{month:02d}/01", "1000", "100000", "10", "11", "9", "10", "0", "5", ""]]

        with patch("historical_backfill.fetch_month", side_effect=fake_fetch) as twse_mock, \
             patch("historical_backfill.fetch_month_tpex") as tpex_mock, \
             patch("historical_backfill.fetch_ex_rights_events", return_value=[]):
            run_backfill(self.history_database, self.imports, self.archive, ["9999"], years=1, as_of=as_of, max_workers=1)
        self.assertEqual(twse_mock.call_count, 19)
        tpex_mock.assert_not_called()

    def test_fetch_month_treats_twse_no_data_stat_as_empty_not_failure(self) -> None:
        """Covers not-yet-listed / already-delisted / nonexistent codes: TWSE
        returns this exact stat for a query period with no matching rows."""
        fake_response = MagicMock()
        fake_response.read.return_value = json.dumps({"stat": "很抱歉，沒有符合條件的資料!", "total": 0}).encode("utf-8")
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False
        with patch("historical_backfill.urlopen", return_value=fake_response):
            records = fetch_month("0000", 2024, 6)
        self.assertEqual(records, [])

    def test_fetch_month_raises_for_genuine_error_stat(self) -> None:
        fake_response = MagicMock()
        fake_response.read.return_value = json.dumps({"stat": "SOME_UNEXPECTED_ERROR"}).encode("utf-8")
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False
        with patch("historical_backfill.urlopen", return_value=fake_response):
            with self.assertRaises(ValueError):
                fetch_month("2330", 2024, 6)

    def test_run_backfill_marks_no_data_month_as_done_not_failed(self) -> None:
        """A permanently-empty period (pre-IPO / post-delisting / nonexistent
        code) must be marked done so it is not retried forever, unlike a real
        transient failure."""
        as_of = date(2026, 7, 24)  # years=1 -> 19 months, see test_run_backfill_persists_progress_and_resumes
        with patch("historical_backfill.fetch_month", return_value=[]), patch("historical_backfill.fetch_ex_rights_events", return_value=[]):
            summary = run_backfill(self.history_database, self.imports, self.archive, ["2330"], years=1, as_of=as_of, max_workers=1)
        self.assertEqual(summary.succeeded, 19)
        self.assertEqual(summary.failed, ())

        with patch("historical_backfill.fetch_month") as mocked, patch("historical_backfill.fetch_ex_rights_events", return_value=[]):
            second = run_backfill(self.history_database, self.imports, self.archive, ["2330"], years=1, as_of=as_of, max_workers=1)
        self.assertEqual(second.attempted, 0)
        mocked.assert_not_called()

    def test_run_backfill_fetches_ex_rights_once_per_distinct_year(self) -> None:
        """years=1 spans two distinct calendar years (last completed year +
        partial current year up to as_of), so the once-per-year ex-rights
        fetch legitimately fires twice here -- once per real distinct year,
        never twice for the same year."""
        as_of = date(2026, 7, 24)

        def fake_fetch(symbol, year, month):
            return [[f"{year - 1911}/{month:02d}/01", "1000", "100000", "10", "11", "9", "10", "0", "5", ""]]

        with patch("historical_backfill.fetch_month", side_effect=fake_fetch), \
             patch("historical_backfill.fetch_ex_rights_events", return_value=[]) as ex_rights_mock:
            run_backfill(self.history_database, self.imports, self.archive, ["2330"], years=1, as_of=as_of, max_workers=1)
        self.assertEqual(ex_rights_mock.call_count, 2)
        called_years = {call.args[0].year for call in ex_rights_mock.call_args_list}
        self.assertEqual(called_years, {2025, 2026})

    def test_run_backfill_retries_a_transient_failure_before_giving_up(self) -> None:
        """A month that fails once but eventually succeeds must end up
        "done", not "failed" -- the whole point of MAX_RETRIES is to absorb
        exactly this (a transient network blip or brief rate limit), not
        just count how many times something failed. Every pending month
        (years=1 spans 13: all of 2025 plus 2026-01) fails on its first
        attempt only, so this also confirms retries are tracked per-month,
        not globally."""
        as_of = date(2026, 1, 15)
        attempts_by_month: dict[tuple[int, int], int] = {}

        def flaky_then_ok(symbol, year, month):
            key = (year, month)
            attempts_by_month[key] = attempts_by_month.get(key, 0) + 1
            if attempts_by_month[key] < 2:
                raise RuntimeError("simulated transient network error")
            return [[f"{year - 1911}/{month:02d}/01", "1000", "100000", "10", "11", "9", "10", "0", "5", ""]]

        with patch("historical_backfill.fetch_month", side_effect=flaky_then_ok), \
             patch("historical_backfill.fetch_ex_rights_events", return_value=[]), \
             patch("historical_backfill.time.sleep"):  # skip the real backoff delay in this test
            summary = run_backfill(self.history_database, self.imports, self.archive, ["2330"], years=1, as_of=as_of, max_workers=1)
        self.assertGreater(summary.attempted, 0)
        self.assertEqual(summary.succeeded, summary.attempted)
        self.assertEqual(summary.failed, ())
        self.assertTrue(all(count == 2 for count in attempts_by_month.values()))  # failed once, retried, then succeeded

    def test_run_backfill_gives_up_after_max_retries_and_marks_failed(self) -> None:
        as_of = date(2026, 1, 15)
        attempts_by_month: dict[tuple[int, int], int] = {}

        def always_fails(symbol, year, month):
            attempts_by_month[(year, month)] = attempts_by_month.get((year, month), 0) + 1
            raise RuntimeError("simulated persistent network error")

        with patch("historical_backfill.fetch_month", side_effect=always_fails), \
             patch("historical_backfill.fetch_ex_rights_events", return_value=[]), \
             patch("historical_backfill.time.sleep"):
            summary = run_backfill(self.history_database, self.imports, self.archive, ["2330"], years=1, as_of=as_of, max_workers=1)
        self.assertEqual(summary.succeeded, 0)
        self.assertEqual(len(summary.failed), summary.attempted)
        from historical_backfill import MAX_RETRIES
        self.assertTrue(all(count == MAX_RETRIES for count in attempts_by_month.values()))

    def test_run_backfill_with_multiple_workers_still_gets_every_month_exactly_once(self) -> None:
        """The concurrency itself must not lose, duplicate, or corrupt work:
        every distinct month gets fetched exactly once and every result is
        accounted for, regardless of which worker happened to pick it up."""
        as_of = date(2026, 7, 24)  # years=1 -> 19 months
        seen_months: set[tuple[int, int]] = set()
        lock = __import__("threading").Lock()

        def fake_fetch(symbol, year, month):
            with lock:
                key = (year, month)
                if key in seen_months:
                    raise AssertionError(f"{key} was fetched more than once")
                seen_months.add(key)
            return [[f"{year - 1911}/{month:02d}/01", "1000", "100000", "10", "11", "9", "10", "0", "5", ""]]

        with patch("historical_backfill.fetch_month", side_effect=fake_fetch), \
             patch("historical_backfill.fetch_ex_rights_events", return_value=[]):
            summary = run_backfill(self.history_database, self.imports, self.archive, ["2330"], years=1, as_of=as_of, max_workers=3)
        self.assertEqual(summary.attempted, 19)
        self.assertEqual(summary.succeeded, 19)
        self.assertEqual(len(seen_months), 19)
        with database_connection(self.history_database) as connection:
            done_count = connection.execute("SELECT COUNT(*) FROM backfill_progress WHERE symbol='2330' AND status='done'").fetchone()[0]
        self.assertEqual(done_count, 19)


if __name__ == "__main__":
    unittest.main()
