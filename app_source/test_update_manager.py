"""Tests for data update schedule/status persistence."""

import sqlite3
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from notification_center import list_notifications
from update_manager import (
    list_statuses,
    record_status,
    run_all_public_daily_updates,
    run_manual_update,
    run_startup_check,
)


class UpdateManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_update_status.sqlite")
        self.imports = Path("data/test_update_imports"); self.imports.mkdir(parents=True, exist_ok=True)
        self.archive = Path("data/test_update_archive"); self.archive.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE IF EXISTS data_update_status")

    def test_records_latest_update_time(self) -> None:
        moment = datetime(2026, 7, 22, tzinfo=timezone.utc)
        record_status(self.database, "TWSE 日行情", "成功", "2 筆資料", moment)
        status = next(item for item in list_statuses(self.database) if item.source == "TWSE 日行情")
        self.assertEqual(status.last_updated_at, moment)
        self.assertEqual(status.status, "成功")

    def test_run_manual_update_records_failure_without_raising_when_fetch_breaks(self) -> None:
        """The exact "updated part has a problem" scenario: a source's
        fetcher throws (network error, unexpected API shape, ...). This must
        never propagate -- it has to come back as a normal string result and
        a "失敗" status the user can see and retry, not crash the caller."""
        with patch("update_manager.fetch_twse", side_effect=RuntimeError("simulated TWSE outage")):
            message = run_manual_update("TWSE 日行情", self.database, self.imports, self.archive)
        self.assertIn("更新失敗", message)
        self.assertIn("simulated TWSE outage", message)
        status = next(item for item in list_statuses(self.database) if item.source == "TWSE 日行情")
        self.assertEqual(status.status, "失敗")
        self.assertIn("simulated TWSE outage", status.detail)

    def test_run_all_public_daily_updates_continues_after_one_source_fails(self) -> None:
        """A failure in one source (here: TWSE) must not stop the others
        (TPEx, VIX) from being attempted and recorded -- a partial update
        should leave exactly the failed source flagged, not silently skip
        or abort the rest of the batch."""
        fake_tpex_records = [{"Code": "6182", "Open": "10", "High": "11", "Low": "9", "Close": "10.5", "TradingShares": "1000"}]
        fake_vix_csv = b"observation_date,VIXCLS\n2026-06-01,15.50\n"
        with patch("update_manager.fetch_twse", side_effect=RuntimeError("simulated TWSE outage")), \
             patch("update_manager.fetch_tpex", return_value=fake_tpex_records), \
             patch("update_manager.fetch_fred_vix_csv", return_value=fake_vix_csv):
            summary = run_all_public_daily_updates(self.database, self.imports, self.archive)
        self.assertIn("更新失敗", summary)
        statuses = {item.source: item.status for item in list_statuses(self.database)}
        self.assertEqual(statuses["TWSE 日行情"], "失敗")
        self.assertEqual(statuses["TPEx 日行情"], "成功")
        self.assertEqual(statuses["VIX／全球風險"], "成功")

    def test_ex_rights_fetch_failure_is_recorded_as_a_notification_not_silently_swallowed(self) -> None:
        """Regression test: a failure fetching ex-dividend/ex-rights events
        (confirmed live: TWSE's endpoint can return a bare 307) used to be a
        bare "except: pass" -- dividend-adjusted prices could silently go
        stale with zero visibility anywhere in the app. It must now be
        recorded as a durable notification when a decision_database is
        available, without breaking the rest of the daily update."""
        decision_database = Path("data/test_update_status_decision.sqlite")
        decision_database.unlink(missing_ok=True)
        with patch("update_manager.fetch_ex_rights_events", side_effect=RuntimeError("simulated 307")):
            summary = run_all_public_daily_updates(self.database, self.imports, self.archive, decision_database)
        self.assertIsInstance(summary, str)  # the rest of the update still completes normally
        records = list_notifications(decision_database)
        self.assertTrue(any(r.category == "ex_rights_fetch_failed" for r in records))

    def test_ex_rights_fetch_failure_does_not_crash_when_no_decision_database_is_given(self) -> None:
        with patch("update_manager.fetch_ex_rights_events", side_effect=RuntimeError("simulated 307")):
            summary = run_all_public_daily_updates(self.database, self.imports, self.archive)
        self.assertIsInstance(summary, str)

    def test_daily_update_fetches_and_stores_market_indices(self) -> None:
        """Regression test: fetch_twse_index/fetch_tpex_index/
        import_market_indices existed (used by market_context_factor_score)
        but were never actually called anywhere -- confirmed by running the
        real app: 個股評分輸入's 情緒指標(自動建議) stayed stuck at the
        neutral 50 fallback forever because market_index_history was never
        populated. The daily update must now fetch and store both."""
        with patch("update_manager.fetch_twse", side_effect=RuntimeError("skip TWSE daily bars")), \
             patch("update_manager.fetch_tpex", side_effect=RuntimeError("skip TPEx daily bars")), \
             patch("update_manager.fetch_fred_vix_csv", side_effect=RuntimeError("skip VIX")), \
             patch("update_manager.fetch_twse_index", return_value=16000.0), \
             patch("update_manager.fetch_tpex_index", return_value=[(date(2026, 8, 4), 200.0)]):
            run_all_public_daily_updates(self.database, self.imports, self.archive)
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute("SELECT market, close_value FROM market_index_history ORDER BY market").fetchall()
        self.assertIn(("TPEx", 200.0), rows)
        self.assertIn(("TWSE", 16000.0), rows)

    def test_market_index_fetch_failure_is_recorded_as_a_notification(self) -> None:
        decision_database = Path("data/test_update_status_decision_index.sqlite")
        decision_database.unlink(missing_ok=True)
        with patch("update_manager.fetch_twse_index", side_effect=RuntimeError("simulated outage")):
            summary = run_all_public_daily_updates(self.database, self.imports, self.archive, decision_database)
        self.assertIsInstance(summary, str)
        records = list_notifications(decision_database)
        self.assertTrue(any(r.category == "market_index_fetch_failed" for r in records))

    def test_new_startup_check_schedules_and_resilience(self) -> None:
        """Verify that the new REVERSAL and DRIFT checks are called, and a failure in one
        does not prevent the other from completing.
        """
        now = datetime(2026, 7, 22, 6, tzinfo=timezone.utc)
        decision_database = Path("data/test_update_decision.sqlite")
        if decision_database.exists():
            decision_database.unlink()

        try:
            # 1. Test normal execution of both checks
            with patch("update_manager.list_statuses", return_value=[]), \
                 patch("update_manager.verify_archive", return_value=[]), \
                 patch("update_manager.run_manual_update"), \
                 patch("notification_center.check_short_term_reversal_triggers", return_value=["msg1"]) as mock_reversal, \
                 patch("notification_center.check_allocation_drift", return_value=["msg2"]) as mock_drift:

                result = run_startup_check(self.database, self.imports, self.archive, now, decision_database=decision_database)
                self.assertIn("短期反彈檢查完成", result)
                self.assertIn("配置偏離檢查完成", result)
                mock_reversal.assert_called_once()
                mock_drift.assert_called_once()

                # Status should be updated to "成功"
                statuses = {item.source: item.status for item in list_statuses(self.database)}
                self.assertEqual(statuses["REVERSAL 短期反彈檢查"], "成功")
                self.assertEqual(statuses["DRIFT 配置偏離檢查"], "成功")

            # Reset database table for the second test
            with sqlite3.connect(self.database) as connection:
                connection.execute("DELETE FROM data_update_status")

            # 2. Test resilience when one check throws an error
            with patch("update_manager.list_statuses", return_value=[]), \
                 patch("update_manager.verify_archive", return_value=[]), \
                 patch("update_manager.run_manual_update"), \
                 patch("notification_center.check_short_term_reversal_triggers", side_effect=ValueError("simulated reversal failure")) as mock_reversal, \
                 patch("notification_center.check_allocation_drift", return_value=["msg2"]) as mock_drift:

                result = run_startup_check(self.database, self.imports, self.archive, now, decision_database=decision_database)
                # Reversal check fails but drift check still runs and is appended to result
                self.assertIn("配置偏離檢查完成", result)
                mock_reversal.assert_called_once()
                mock_drift.assert_called_once()

                # Reversal status should be "失敗", drift status should be "成功"
                statuses = {item.source: item.status for item in list_statuses(self.database)}
                self.assertEqual(statuses["REVERSAL 短期反彈檢查"], "失敗")
                self.assertEqual(statuses["DRIFT 配置偏離檢查"], "成功")

        finally:
            if decision_database.exists():
                decision_database.unlink()
