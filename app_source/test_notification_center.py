"""Tests for the proactive notification log and watchlist-trigger scanner."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from notification_center import check_watchlist_triggers, list_notifications, record_notification
from transaction_ledger import set_current_price
from watchlist_repository import add_item


class NotificationCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_notification_center.sqlite")
        if self.database.exists():
            self.database.unlink()
        self.now = datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def test_record_and_list_round_trip(self) -> None:
        record_notification(self.database, "test", "2330", "測試訊息", self.now, notify_os=False)
        records = list_notifications(self.database)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].symbol, "2330")
        self.assertEqual(records[0].message, "測試訊息")

    def test_duplicate_within_window_is_not_relogged(self) -> None:
        first = record_notification(self.database, "test", "2330", "同一則訊息", self.now, notify_os=False)
        second = record_notification(self.database, "test", "2330", "同一則訊息", self.now + timedelta(minutes=5), notify_os=False)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(list_notifications(self.database)), 1)

    def test_same_message_outside_window_is_relogged(self) -> None:
        record_notification(self.database, "test", "2330", "同一則訊息", self.now, notify_os=False, dedupe_within=timedelta(hours=1))
        second = record_notification(self.database, "test", "2330", "同一則訊息", self.now + timedelta(hours=2), notify_os=False, dedupe_within=timedelta(hours=1))
        self.assertIsNotNone(second)
        self.assertEqual(len(list_notifications(self.database)), 2)

    def test_rejects_missing_category_or_naive_datetime(self) -> None:
        with self.assertRaises(ValueError):
            record_notification(self.database, "", "2330", "msg", self.now, notify_os=False)
        with self.assertRaises(ValueError):
            record_notification(self.database, "test", "2330", "msg", datetime(2026, 7, 25), notify_os=False)

    def test_watchlist_target_hit_fires_a_notification(self) -> None:
        add_item(self.database, "2330", "台積電", 100.0, 120.0, 90.0, self.now)
        set_current_price(self.database, "2330", 125.0, self.now)
        fired = check_watchlist_triggers(self.database, self.now, notify_os=False)
        self.assertEqual(len(fired), 1)
        self.assertIn("停利", fired[0])
        records = list_notifications(self.database)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].category, "watchlist_trigger")

    def test_watchlist_stop_hit_fires_a_notification(self) -> None:
        add_item(self.database, "2330", "台積電", 100.0, 120.0, 90.0, self.now)
        set_current_price(self.database, "2330", 85.0, self.now)
        fired = check_watchlist_triggers(self.database, self.now, notify_os=False)
        self.assertEqual(len(fired), 1)
        self.assertIn("停損", fired[0])

    def test_watchlist_item_within_range_does_not_fire(self) -> None:
        add_item(self.database, "2330", "台積電", 100.0, 120.0, 90.0, self.now)
        set_current_price(self.database, "2330", 105.0, self.now)
        fired = check_watchlist_triggers(self.database, self.now, notify_os=False)
        self.assertEqual(fired, [])
        self.assertEqual(list_notifications(self.database), [])

    def test_watchlist_item_without_a_current_price_is_skipped(self) -> None:
        add_item(self.database, "2330", "台積電", 100.0, 120.0, 90.0, self.now)
        fired = check_watchlist_triggers(self.database, self.now, notify_os=False)
        self.assertEqual(fired, [])

    def test_repeated_scan_does_not_duplicate_the_same_trigger(self) -> None:
        add_item(self.database, "2330", "台積電", 100.0, 120.0, 90.0, self.now)
        set_current_price(self.database, "2330", 125.0, self.now)
        check_watchlist_triggers(self.database, self.now, notify_os=False)
        fired_again = check_watchlist_triggers(self.database, self.now + timedelta(minutes=10), notify_os=False)
        self.assertEqual(fired_again, [])
        self.assertEqual(len(list_notifications(self.database)), 1)

    def test_send_os_notification_degrades_gracefully_when_toast_fails(self) -> None:
        from notification_center import send_os_notification
        with patch("win11toast.notify", side_effect=RuntimeError("no toast backend")):
            self.assertFalse(send_os_notification("title", "body"))


if __name__ == "__main__":
    unittest.main()
