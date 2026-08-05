"""Tests for data update schedule/status persistence."""

import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from update_manager import list_statuses, record_status, run_all_public_daily_updates, run_manual_update


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
