"""Tests for the proactive notification log and watchlist-trigger scanner."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from notification_center import (
    check_allocation_drift,
    check_watchlist_triggers,
    check_short_term_reversal_triggers,
    list_notifications,
    record_notification,
)
from transaction_ledger import set_current_price
from watchlist_repository import add_item


class NotificationCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_notification_center.sqlite")
        self.history_database = Path("test_notification_center_history.sqlite")
        for db in (self.database, self.history_database):
            if db.exists():
                db.unlink()
        self.now = datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        for db in (self.database, self.history_database):
            if db.exists():
                db.unlink()

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

    def test_check_allocation_drift_exceeds_threshold(self) -> None:
        # 1. Setup history database with securities
        from security_catalog import ensure_schema as ensure_securities_schema
        from database_utils import database_connection
        with database_connection(self.history_database) as conn:
            ensure_securities_schema(conn)
            conn.execute(
                "INSERT INTO securities (symbol, name, market, sector, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                ("2330", "台積電", "TWSE", "半導體業", "2026-07-25", "2026-07-25")
            )

        # 2. Add transaction and price
        from transaction_ledger import Transaction, add_transaction, set_current_price
        add_transaction(self.database, Transaction(None, "Will", "2330", self.now, "BUY", 100, 100, 0))
        set_current_price(self.database, "2330", 100, self.now)

        # 3. Seed factor score manually above minimum score (e.g. 80)
        from factor_score_store import MANUAL_FACTOR_NAMES, save_factor_scores
        factors = {name: 80.0 for name in MANUAL_FACTOR_NAMES}
        save_factor_scores(self.database, "2330", self.now, factors, 20, {})

        # Since 2330 is the only holding, current weight is 100%.
        # Target weight is capped at 20.0% by portfolio_risk_rules.json.
        # Drift is |100% - 20%| = 80%.
        # With threshold_pct=5.0, drift (80%) > threshold (5.0%) -> triggers.
        fired = check_allocation_drift(self.database, self.history_database, threshold_pct=5.0, now=self.now, notify_os=False)
        self.assertEqual(len(fired), 1)
        self.assertIn("2330", fired[0])
        self.assertIn("目前權重 100.0% 偏離目標 20.0%", fired[0])

        # Verify logged notification
        records = list_notifications(self.database)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].category, "allocation_drift")
        self.assertEqual(records[0].symbol, "2330")

    def test_check_allocation_drift_below_threshold(self) -> None:
        from security_catalog import ensure_schema as ensure_securities_schema
        from database_utils import database_connection
        with database_connection(self.history_database) as conn:
            ensure_securities_schema(conn)
            conn.execute(
                "INSERT INTO securities (symbol, name, market, sector, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                ("2330", "台積電", "TWSE", "半導體業", "2026-07-25", "2026-07-25")
            )

        from transaction_ledger import Transaction, add_transaction, set_current_price
        add_transaction(self.database, Transaction(None, "Will", "2330", self.now, "BUY", 100, 100, 0))
        set_current_price(self.database, "2330", 100, self.now)

        from factor_score_store import MANUAL_FACTOR_NAMES, save_factor_scores
        factors = {name: 80.0 for name in MANUAL_FACTOR_NAMES}
        save_factor_scores(self.database, "2330", self.now, factors, 20, {})

        # Drift is 80%. With threshold_pct=90.0, drift (80%) <= threshold (90%) -> does not trigger.
        fired = check_allocation_drift(self.database, self.history_database, threshold_pct=90.0, now=self.now, notify_os=False)
        self.assertEqual(len(fired), 0)
        records = list_notifications(self.database)
        self.assertEqual(len(records), 0)

    def test_check_allocation_drift_multiple_owners(self) -> None:
        from security_catalog import ensure_schema as ensure_securities_schema
        from database_utils import database_connection
        with database_connection(self.history_database) as conn:
            ensure_securities_schema(conn)
            conn.execute(
                "INSERT INTO securities (symbol, name, market, sector, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                ("2330", "台積電", "TWSE", "半導體業", "2026-07-25", "2026-07-25")
            )
            conn.execute(
                "INSERT INTO securities (symbol, name, market, sector, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                ("2317", "鴻海", "TWSE", "電子業", "2026-07-25", "2026-07-25")
            )

        from transaction_ledger import Transaction, add_transaction, set_current_price
        # Will has 2330
        add_transaction(self.database, Transaction(None, "Will", "2330", self.now, "BUY", 100, 100, 0))
        # Sarah has 2317
        add_transaction(self.database, Transaction(None, "Sarah", "2317", self.now, "BUY", 100, 100, 0))

        set_current_price(self.database, "2330", 100, self.now)
        set_current_price(self.database, "2317", 100, self.now)

        from factor_score_store import MANUAL_FACTOR_NAMES, save_factor_scores
        factors = {name: 80.0 for name in MANUAL_FACTOR_NAMES}
        save_factor_scores(self.database, "2330", self.now, factors, 20, {})
        save_factor_scores(self.database, "2317", self.now, factors, 20, {})

        fired = check_allocation_drift(self.database, self.history_database, threshold_pct=5.0, now=self.now, notify_os=False)
        self.assertEqual(len(fired), 2)
        records = list_notifications(self.database)
        self.assertEqual(len(records), 2)
        symbols_logged = {r.symbol for r in records}
        self.assertEqual(symbols_logged, {"2330", "2317"})

    def test_check_allocation_drift_deduplication(self) -> None:
        from security_catalog import ensure_schema as ensure_securities_schema
        from database_utils import database_connection
        with database_connection(self.history_database) as conn:
            ensure_securities_schema(conn)
            conn.execute(
                "INSERT INTO securities (symbol, name, market, sector, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                ("2330", "台積電", "TWSE", "半導體業", "2026-07-25", "2026-07-25")
            )

        from transaction_ledger import Transaction, add_transaction, set_current_price
        add_transaction(self.database, Transaction(None, "Will", "2330", self.now, "BUY", 100, 100, 0))
        set_current_price(self.database, "2330", 100, self.now)

        from factor_score_store import MANUAL_FACTOR_NAMES, save_factor_scores
        factors = {name: 80.0 for name in MANUAL_FACTOR_NAMES}
        save_factor_scores(self.database, "2330", self.now, factors, 20, {})

        # First call triggers notification
        fired = check_allocation_drift(self.database, self.history_database, threshold_pct=5.0, now=self.now, notify_os=False)
        self.assertEqual(len(fired), 1)

        # Second call on same day should be deduplicated
        fired_again = check_allocation_drift(self.database, self.history_database, threshold_pct=5.0, now=self.now, notify_os=False)
        self.assertEqual(len(fired_again), 0)

        # Same call on a different day (now + 1 day) should NOT be deduplicated
        next_day = self.now + timedelta(days=1)
        fired_next_day = check_allocation_drift(self.database, self.history_database, threshold_pct=5.0, now=next_day, notify_os=False)
        self.assertEqual(len(fired_next_day), 1)

        # Total logged should be 2
        records = list_notifications(self.database)
        self.assertEqual(len(records), 2)

    def test_check_allocation_drift_ignores_candidates_and_zero_shares(self) -> None:
        # If there are no owned shares, but there's a watchlist item, it should not trigger drift notification
        from watchlist_repository import add_item
        add_item(self.database, "2330", "台積電", 100.0, 120.0, 90.0, self.now)

        from security_catalog import ensure_schema as ensure_securities_schema
        from database_utils import database_connection
        with database_connection(self.history_database) as conn:
            ensure_securities_schema(conn)
            conn.execute(
                "INSERT INTO securities (symbol, name, market, sector, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                ("2330", "台積電", "TWSE", "半導體業", "2026-07-25", "2026-07-25")
            )

        from factor_score_store import MANUAL_FACTOR_NAMES, save_factor_scores
        factors = {name: 80.0 for name in MANUAL_FACTOR_NAMES}
        save_factor_scores(self.database, "2330", self.now, factors, 20, {})

        fired = check_allocation_drift(self.database, self.history_database, threshold_pct=5.0, now=self.now, notify_os=False)
        # Should not fire because no owner holds 2330 (ledger is empty, so no owners)
        self.assertEqual(len(fired), 0)
        self.assertEqual(len(list_notifications(self.database)), 0)

    def test_check_allocation_drift_skips_holdings_without_a_saved_score(self) -> None:
        # An owned symbol with no factor score on record must not be treated as
        # "100% drift" just because build_allocation_plan defaults its target to 0%.
        from security_catalog import ensure_schema as ensure_securities_schema
        from database_utils import database_connection
        with database_connection(self.history_database) as conn:
            ensure_securities_schema(conn)
            conn.execute(
                "INSERT INTO securities (symbol, name, market, sector, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                ("2330", "台積電", "TWSE", "半導體業", "2026-07-25", "2026-07-25")
            )

        from transaction_ledger import Transaction, add_transaction, set_current_price
        add_transaction(self.database, Transaction(None, "Will", "2330", self.now, "BUY", 100, 100, 0))
        set_current_price(self.database, "2330", 100, self.now)
        # Deliberately no save_factor_scores(...) call for 2330.

        fired = check_allocation_drift(self.database, self.history_database, threshold_pct=5.0, now=self.now, notify_os=False)
        self.assertEqual(len(fired), 0)
        self.assertEqual(len(list_notifications(self.database)), 0)

    def _seed_daily_bars(self, symbol: str, prices: list[float]) -> None:
        import sqlite3
        self.history_database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.history_database)
        try:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS daily_bars (
                    symbol TEXT NOT NULL, trading_date TEXT NOT NULL,
                    open_micros INTEGER NOT NULL, high_micros INTEGER NOT NULL,
                    low_micros INTEGER NOT NULL, close_micros INTEGER NOT NULL,
                    volume INTEGER NOT NULL, source TEXT NOT NULL, published_at TEXT NOT NULL,
                    import_checksum TEXT NOT NULL,
                    PRIMARY KEY(symbol, trading_date, source)
                )
            """)
            for idx, price in enumerate(prices):
                date = f"2026-01-{1 + idx:02d}"
                connection.execute(
                    "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST', ?, 'chk')",
                    (symbol, date, int((price - 1) * 1_000_000), int((price + 1) * 1_000_000),
                     int((price - 2) * 1_000_000), int(price * 1_000_000), 1_000_000, f"{date}T13:30:00+08:00"),
                )
            connection.commit()
        finally:
            connection.close()

    def test_check_short_term_reversal_triggers_normal(self) -> None:
        add_item(self.database, "2330", "台積電", 100.0, 120.0, 90.0, self.now)
        # Seeds prices: drop from 100 to 90 is 10% (>= 8%)
        self._seed_daily_bars("2330", [100.0, 99.0, 98.0, 97.0, 95.0, 90.0])

        fired = check_short_term_reversal_triggers(
            self.database, self.history_database, self.now, lookback=5, drop_pct=8.0, notify_os=False
        )
        self.assertEqual(len(fired), 1)
        self.assertIn("2330 近5日跌幅達8%以上", fired[0])

        records = list_notifications(self.database)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].category, "short_term_reversal")
        self.assertEqual(records[0].symbol, "2330")

    def test_check_short_term_reversal_triggers_insufficient_data(self) -> None:
        add_item(self.database, "2330", "台積電", 100.0, 120.0, 90.0, self.now)
        self._seed_daily_bars("2330", [100.0, 90.0]) # only 2 bars, lookback 5 needs 6 bars

        fired = check_short_term_reversal_triggers(
            self.database, self.history_database, self.now, lookback=5, drop_pct=8.0, notify_os=False
        )
        self.assertEqual(len(fired), 0)

    def test_check_short_term_reversal_triggers_no_trigger(self) -> None:
        add_item(self.database, "2330", "台積電", 100.0, 120.0, 90.0, self.now)
        self._seed_daily_bars("2330", [100.0, 99.0, 98.0, 97.0, 96.0, 95.0]) # drop 5% (< 8%)

        fired = check_short_term_reversal_triggers(
            self.database, self.history_database, self.now, lookback=5, drop_pct=8.0, notify_os=False
        )
        self.assertEqual(len(fired), 0)

    def test_check_short_term_reversal_triggers_deduplication(self) -> None:
        add_item(self.database, "2330", "台積電", 100.0, 120.0, 90.0, self.now)
        self._seed_daily_bars("2330", [100.0, 99.0, 98.0, 97.0, 95.0, 90.0])

        fired = check_short_term_reversal_triggers(
            self.database, self.history_database, self.now, lookback=5, drop_pct=8.0, notify_os=False
        )
        self.assertEqual(len(fired), 1)

        # Call again on the same day -> deduplicated
        fired_again = check_short_term_reversal_triggers(
            self.database, self.history_database, self.now, lookback=5, drop_pct=8.0, notify_os=False
        )
        self.assertEqual(len(fired_again), 0)

        # Call on next day -> triggers again
        next_day = self.now + timedelta(days=1)
        fired_next_day = check_short_term_reversal_triggers(
            self.database, self.history_database, next_day, lookback=5, drop_pct=8.0, notify_os=False
        )
        self.assertEqual(len(fired_next_day), 1)


if __name__ == "__main__":
    unittest.main()
