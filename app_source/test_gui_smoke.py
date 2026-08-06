"""Non-interactive desktop smoke test: construct every integrated tab and refresh it."""
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
try:
    import tkinter as tk
    from stock_ai_app import StockAiApp
    from notification_center import list_notifications
except Exception: tk = None

@unittest.skipIf(tk is None, "Tk is unavailable")
class GuiSmokeTests(unittest.TestCase):
    def _get_tab(self, notebook, class_name):
        for tab in notebook.tabs():
            widget = notebook.nametowidget(tab)
            if widget.__class__.__name__ == class_name:
                return widget
        raise ValueError(f"Tab of class {class_name} not found")

    def test_dashboard_constructs_all_tabs(self):
        base = Path("data/test_gui_storage"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), patch("holdings_manager.storage_paths", return_value=paths), patch("watchlist_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                self.assertEqual(len(notebook.tabs()), 12)
                for tab in notebook.tabs(): notebook.select(tab); root.update_idletasks()
            finally:
                root.destroy()

    def test_switching_to_watchlist_tab_auto_refreshes_without_a_manual_click(self):
        """A score saved on 個股評分輸入 must show up on 自選追蹤 as soon as
        you switch to it -- previously only an explicit 重新整理 click
        re-read the data, so switching tabs showed stale "待輸入現價／評分"
        even right after saving a real score."""
        from historical_storage import archive_and_import, DailyBar
        from security_catalog import upsert_from_daily_snapshot
        from twse_daily_importer import write_normalized_csv
        from watchlist_repository import add_item

        base = Path("data/test_gui_tab_refresh"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        upsert_from_daily_snapshot(paths["history_database"], [("6182", "合晶")], "TPEx", "2026-07-30T00:00:00")
        bars = [DailyBar("6182", date(2026, 7, 30), 84.0, 85.0, 83.0, 84.7, 20000, "TEST", datetime(2026, 7, 30, tzinfo=timezone.utc))]
        csv_path = paths["imports"]; csv_path.mkdir(parents=True, exist_ok=True); csv_path = csv_path / "seed.csv"
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, paths["history_database"], paths["raw_archive"])
        add_item(paths["decision_database"], "6182", "合晶", 42.0, 42.0, 42.0, datetime.now().astimezone())

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths), \
             patch("factor_score_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                watchlist_frame = self._get_tab(notebook, "WatchlistApp")
                score_frame = self._get_tab(notebook, "FactorScoreApp")

                notebook.select(watchlist_frame); root.update()
                before = watchlist_frame.table.item(watchlist_frame.table.get_children()[0])["values"]
                self.assertEqual(before[7], "—")

                notebook.select(score_frame); root.update()
                score_frame.query.delete(0, "end"); score_frame.query.insert(0, "6182")
                score_frame.lookup_symbol(); score_frame.save()

                notebook.select(watchlist_frame); root.update()
                after = watchlist_frame.table.item(watchlist_frame.table.get_children()[0])["values"]
                self.assertNotEqual(after[7], "—")
            finally:
                root.destroy()

    def test_price_chart_tab_shows_a_real_chart_for_a_tracked_symbol(self):
        from historical_storage import DailyBar, archive_and_import
        from security_catalog import upsert_from_daily_snapshot
        from twse_daily_importer import write_normalized_csv

        base = Path("data/test_gui_price_chart"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        upsert_from_daily_snapshot(paths["history_database"], [("2330", "台積電")], "TWSE", "2026-07-30T00:00:00")
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        bars = [DailyBar("2330", date(2026, 7, 20 + i), 100 + i, 105 + i, 95 + i, 100 + i, 1000, "TEST", now) for i in range(3)]
        csv_path = paths["imports"]; csv_path.mkdir(parents=True, exist_ok=True); csv_path = csv_path / "seed.csv"
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, paths["history_database"], paths["raw_archive"])

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                chart_frame = self._get_tab(notebook, "PriceChartFrame")

                chart_frame.symbol_entry.insert(0, "2330")
                chart_frame.window_choice.set("近30天")
                chart_frame.show_chart()
                root.update_idletasks()

                self.assertIn("2330", chart_frame.status["text"])
                self.assertIn("3", chart_frame.status["text"])  # 3 trading days seeded
                self.assertTrue(chart_frame.chart_area.winfo_children())  # a canvas was actually embedded
            finally:
                root.destroy()

    def test_backfill_all_tracked_button_clears_manual_symbols_and_uses_holdings_plus_watchlist(self):
        """"回補持股＋自選股全部" must estimate against the real
        holdings+watchlist union, even if the 代號 field still has leftover
        text typed in from a previous, unrelated lookup -- not silently
        estimate for whatever stale text happens to be sitting there."""
        from security_catalog import upsert_from_daily_snapshot
        from watchlist_repository import add_item

        base = Path("data/test_gui_backfill_all"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        upsert_from_daily_snapshot(paths["history_database"], [("6182", "合晶")], "TPEx", "2026-07-30T00:00:00")
        add_item(paths["decision_database"], "6182", "合晶", 42.0, 42.0, 42.0, datetime.now().astimezone())

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                data_frame = self._get_tab(notebook, "DataManagementFrame")

                data_frame.backfill_symbols.delete(0, "end")
                data_frame.backfill_symbols.insert(0, "9999")  # stale leftover text from an unrelated lookup
                data_frame.estimate_backfill_all_tracked()

                self.assertEqual(data_frame.backfill_symbols.get(), "")
                self.assertTrue(data_frame._pending_backfill)
                self.assertTrue(all(symbol == "6182" for symbol, _year, _month in data_frame._pending_backfill))
                self.assertEqual(str(data_frame.start_backfill_button["state"]), "normal")
            finally:
                root.destroy()

    def test_live_backfill_progress_shows_completed_symbol_count_not_just_raw_request_count(self):
        """The LIVE "回補中 X/Y" status line shown while a run is actually in
        progress must also show completed-symbol count -- a real gap found
        by the user: the estimate and the final summary both got the
        per-symbol count, but the live ticker during the run itself still
        only showed the raw, barely-moving request index.

        Makes self.after execute immediately (synchronously) instead of
        queuing through Tk, so intermediate status text can be captured
        mid-run -- otherwise the final done() callback (queued after every
        progress step) would overwrite it before a plain root.update() could
        distinguish "still running" text from "run finished" text."""
        from historical_backfill import BackfillSummary

        base = Path("data/test_gui_live_backfill_progress"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        captured_mid_run_text = {}

        def fake_run_backfill(history_database, imports_directory, archive_directory, symbols, years=10, progress_callback=None, should_stop=None):
            # Symbol AAAA has 2 months pending, BBBB has 1 -- simulate all three completing in order.
            progress_callback(1, 3, "AAAA 2026-01")
            progress_callback(2, 3, "AAAA 2026-02")  # AAAA now fully done
            captured_mid_run_text["text"] = data_frame.backfill_status["text"]  # snapshot before BBBB / before done()
            progress_callback(3, 3, "BBBB 2026-01")  # BBBB now fully done too
            return BackfillSummary(3, 3, (), False)

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths), \
             patch("stock_ai_app.run_backfill", side_effect=fake_run_backfill), \
             patch("stock_ai_app.messagebox.showinfo"):  # done()'s real modal would otherwise block forever headless
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                data_frame = self._get_tab(notebook, "DataManagementFrame")
                data_frame._pending_backfill = [("AAAA", 2026, 1), ("AAAA", 2026, 2), ("BBBB", 2026, 1)]
                data_frame.after = lambda _delay, fn: fn()  # run scheduled callbacks immediately, not via Tk's queue

                data_frame._backfill_worker(["AAAA", "BBBB"], 10)  # run synchronously, like other _worker tests

                self.assertIn("已完成", captured_mid_run_text["text"])
                self.assertIn("1/2 檔股票", captured_mid_run_text["text"])  # only AAAA done at that point
                self.assertIn("回補中 2/3", captured_mid_run_text["text"])
            finally:
                root.destroy()

    def test_estimate_backfill_shows_completed_symbol_count_not_just_raw_request_count(self):
        """A raw "還剩 N 次請求" barely visibly moves for a large symbol list
        even after real progress (any one session only closes a small
        fraction of a ~2000-symbol catalog) -- the per-symbol completed count
        is what actually proves progress persists across runs."""
        from historical_storage import DailyBar, archive_and_import
        from security_catalog import upsert_from_daily_snapshot
        from twse_daily_importer import write_normalized_csv
        from watchlist_repository import add_item

        base = Path("data/test_gui_backfill_progress"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        upsert_from_daily_snapshot(paths["history_database"], [("2330", "台積電"), ("6182", "合晶")], "TWSE", "2026-07-30T00:00:00")
        add_item(paths["decision_database"], "2330", "台積電", 500.0, 500.0, 500.0, datetime.now().astimezone())
        add_item(paths["decision_database"], "6182", "合晶", 42.0, 42.0, 42.0, datetime.now().astimezone())
        # Give 2330 a full year of real bars so it's genuinely "done"; 6182 has none.
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        bars = [DailyBar("2330", date(2026, m, 1), 100, 105, 95, 102, 1000, "TEST", now) for m in range(1, 8)]
        csv_path = paths["imports"]; csv_path.mkdir(parents=True, exist_ok=True); csv_path = csv_path / "seed.csv"
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, paths["history_database"], paths["raw_archive"])

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                data_frame = self._get_tab(notebook, "DataManagementFrame")
                data_frame.backfill_years.delete(0, "end"); data_frame.backfill_years.insert(0, "1")

                data_frame.estimate_backfill_all_tracked()

                self.assertIn("已完成", data_frame.backfill_status["text"])
                self.assertIn("/2 檔股票", data_frame.backfill_status["text"])
            finally:
                root.destroy()

    def test_window_construction_does_not_block_on_startup_checks(self):
        """StockAiApp(root) must return immediately -- previously main()
        called the (real, network-hitting) verify_and_cache/run_startup_check
        synchronously before StockAiApp(root) was even constructed, holding
        up window creation for however long those network calls took (up to
        several minutes if a full daily update was due). Uses begin_startup_check
        with the real worker patched to a no-op: this test is only about
        construction timing and the button disabling synchronously the
        moment the check starts, not the worker's own completion logic
        (covered separately, since real threads calling Tkinter's .after()
        require an active mainloop() that this non-interactive polling-based
        test harness never starts -- a test-infrastructure constraint, not
        something a real user hits)."""
        import time

        base = Path("data/test_gui_startup_check"); base.mkdir(parents=True, exist_ok=True)
        paths = {"root": base, "history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths), \
             patch("stock_ai_app.DataManagementFrame._startup_check_worker", lambda self: None):
            root = tk.Tk(); root.withdraw()
            try:
                start = time.monotonic()
                app = StockAiApp(root); root.update_idletasks()
                construction_elapsed = time.monotonic() - start
                self.assertLess(construction_elapsed, 1.0)

                app.run_startup_checks_in_background()
                self.assertEqual(str(app.data_management_frame.all_update_button["state"]), "disabled")
            finally:
                root.destroy()

    def test_startup_check_worker_reports_results_and_reenables_the_button(self):
        """Runs the worker's real body directly (same thread as the test,
        like every other _worker test in this file) so self.after(0, ...)'s
        callback can be safely processed with a plain update() -- verifies
        the actual completion logic: button re-enabled, status text shows
        both real results."""
        base = Path("data/test_gui_startup_check_worker"); base.mkdir(parents=True, exist_ok=True)
        paths = {"root": base, "history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths), \
             patch("stock_ai_app.verify_and_cache", return_value="測試費率結果"), \
             patch("stock_ai_app.run_startup_check", return_value="測試更新結果"):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                data_frame = app.data_management_frame
                data_frame.all_update_button.configure(state="disabled")

                data_frame._startup_check_worker()
                root.update()  # process the self.after(0, done) callback

                self.assertEqual(str(data_frame.all_update_button["state"]), "normal")
                self.assertIn("測試更新結果", data_frame.progress_status["text"])
                self.assertIn("測試費率結果", data_frame.progress_status["text"])
            finally:
                root.destroy()

    def test_startup_check_worker_logs_a_notification_when_fee_verification_fails(self):
        """Regression test: a failed/unconfirmed fee-rate verification used to
        only ever show in the transient startup status label -- if the user
        wasn't watching at that moment, they'd never know. It must also land
        in the durable notification log."""
        base = Path("data/test_gui_startup_check_fee_failure"); base.mkdir(parents=True, exist_ok=True)
        paths = {"root": base, "history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths), \
             patch("stock_ai_app.verify_and_cache", return_value="凱基費率頁格式未完整辨識；保留既有已驗證費率。"), \
             patch("stock_ai_app.run_startup_check", return_value="測試更新結果"):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                app.data_management_frame._startup_check_worker()
                root.update()
                records = list_notifications(paths["decision_database"])
                self.assertTrue(any(r.category == "fee_rate_verification" for r in records))
            finally:
                root.destroy()

    def test_startup_check_worker_does_not_log_a_notification_when_fee_verification_succeeds(self):
        base = Path("data/test_gui_startup_check_fee_success"); base.mkdir(parents=True, exist_ok=True)
        paths = {"root": base, "history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths), \
             patch("stock_ai_app.verify_and_cache", return_value="凱基公開牌告費率已連線確認。"), \
             patch("stock_ai_app.run_startup_check", return_value="測試更新結果"):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                app.data_management_frame._startup_check_worker()
                root.update()
                records = list_notifications(paths["decision_database"])
                self.assertFalse(any(r.category == "fee_rate_verification" for r in records))
            finally:
                root.destroy()

    def test_full_history_download_button_scopes_to_every_catalogued_symbol(self):
        """"全歷史資料下載" must estimate against every symbol in the
        securities catalog, not just holdings/watchlist -- distinguishing it
        from "回補持股＋自選股全部"."""
        from security_catalog import upsert_from_daily_snapshot

        base = Path("data/test_gui_backfill_all_symbols"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        # Two catalogued symbols, neither in holdings or watchlist.
        upsert_from_daily_snapshot(paths["history_database"], [("2330", "台積電"), ("6182", "合晶")], "TWSE", "2026-07-30T00:00:00")

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                data_frame = self._get_tab(notebook, "DataManagementFrame")

                data_frame.estimate_backfill_all_symbols()

                self.assertEqual(set(data_frame.backfill_symbols.get().split(",")), {"2330", "6182"})
                self.assertTrue(data_frame._pending_backfill)
                self.assertEqual({symbol for symbol, _year, _month in data_frame._pending_backfill}, {"2330", "6182"})
            finally:
                root.destroy()

    def test_integrity_check_button_reports_a_real_scan_result(self):
        from datetime import datetime as dt, timezone as tz
        from historical_storage import DailyBar, archive_and_import
        from twse_daily_importer import write_normalized_csv

        base = Path("data/test_gui_integrity_check"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        now = dt(2026, 7, 24, tzinfo=tz.utc)
        bars = [DailyBar("2330", date(2026, 7, 24), 100, 105, 95, 102, 1000, "TEST", now)]
        csv_path = base / "imports" / "seed.csv"; csv_path.parent.mkdir(parents=True, exist_ok=True)
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, paths["history_database"], paths["raw_archive"])

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                data_frame = self._get_tab(notebook, "DataManagementFrame")

                data_frame._integrity_worker(None)  # run synchronously in-thread for a deterministic test
                root.update()  # self.after(0, ...) callbacks only run via update(), not update_idletasks()

                self.assertIn("已檢查 1", data_frame.integrity_status["text"])
                self.assertIn("無錯誤", data_frame.integrity_status["text"])
            finally:
                root.destroy()

    def test_sample_data_warning_display(self):
        """Test warning banner behaviour in PortfolioRiskFrame and HedgeAdviceFrame.

        (a) When no real positions/current prices are available, a warning displaying
            '示範資料' is shown.
        (b) When real positions with current prices are available, the warning banner is cleared.
        """
        from transaction_ledger import add_transaction, set_current_price, Transaction, initialize

        base = Path("data/test_gui_sample_warning"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)
        initialize(paths["decision_database"])

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                # Scenario A: empty database -> warning displays "示範資料"
                app = StockAiApp(root)
                root.update_idletasks()

                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                tabs = notebook.tabs()

                # Tab index 2 is PortfolioDecisionFrame, which contains PortfolioRiskFrame and HedgeAdviceFrame inside its sub-notebook
                decision_frame = notebook.nametowidget(tabs[2])
                sub_notebook = next(child for child in decision_frame.winfo_children() if child.winfo_class() == "TNotebook")
                sub_tabs = sub_notebook.tabs()
                risk_frame = sub_notebook.nametowidget(sub_tabs[0])
                hedge_frame = sub_notebook.nametowidget(sub_tabs[3])

                # Force refresh to make sure
                risk_frame.refresh()
                hedge_frame.refresh()

                self.assertTrue(risk_frame._using_sample_data)
                self.assertIn("示範資料", risk_frame.sample_data_warn["text"])

                self.assertTrue(hedge_frame._using_sample_data)
                self.assertIn("示範資料", hedge_frame.sample_data_warn["text"])

                # Scenario B: Add a real transaction and a current price -> warning should clear
                t = Transaction(None, "OwnerA", "2330", datetime.now().astimezone(), "BUY", 1000, 500.0)
                add_transaction(paths["decision_database"], t)
                set_current_price(paths["decision_database"], "2330", 520.0, datetime.now().astimezone())

                risk_frame.refresh()
                hedge_frame.refresh()

                self.assertFalse(risk_frame._using_sample_data)
                self.assertEqual("", risk_frame.sample_data_warn["text"])

                self.assertFalse(hedge_frame._using_sample_data)
                self.assertEqual("", hedge_frame.sample_data_warn["text"])
            finally:
                root.destroy()

    def test_position_advice_tab_shows_advice_and_skips_missing(self):
        """📋 個股建議 tab must successfully display advice for scored holdings
        and skip unscored holdings without crashing."""
        from historical_storage import DailyBar, archive_and_import
        from security_catalog import upsert_from_daily_snapshot
        from twse_daily_importer import write_normalized_csv
        from transaction_ledger import add_transaction, set_current_price, Transaction
        from factor_score_store import save_factor_scores

        base = Path("data/test_gui_position_advice"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        # Seed two stocks: 2330 and 2303.
        # 2330 will have both scores and holdings.
        # 2303 will have holdings but NO scores.
        upsert_from_daily_snapshot(paths["history_database"], [("2330", "台積電"), ("2303", "聯電")], "TWSE", "2026-07-30T00:00:00")

        # Add transactions to ledger (buy shares > 0)
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        add_transaction(paths["decision_database"], Transaction(None, "Will", "2330", now, "BUY", 1000, 500.0, 100.0, "Initial"))
        add_transaction(paths["decision_database"], Transaction(None, "Will", "2303", now, "BUY", 1000, 50.0, 10.0, "Initial"))

        # Set current prices
        set_current_price(paths["decision_database"], "2330", 550.0, now)
        set_current_price(paths["decision_database"], "2303", 55.0, now)

        # Seed factor scores ONLY for 2330, NOT 2303
        from weighted_analysis import FACTOR_NAMES
        factors_2330 = {name: 95.0 for name in FACTOR_NAMES if name != "technical"}
        save_factor_scores(paths["decision_database"], "2330", now, factors_2330, 20.0, {"_note": "Test 2330"})

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths), \
             patch("factor_score_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")

                # Retrieve the position advice frame
                advice_frame = self._get_tab(notebook, "PositionAdviceFrame")
                advice_frame.refresh()
                root.update()

                # Verify 2330 was successfully analyzed and inserted in treeview
                children = advice_frame.table.get_children()
                self.assertEqual(len(children), 1)  # only 2330 because 2303 lacks scores and is skipped

                # Verify 2330's values
                values = advice_frame.table.item(children[0])["values"]
                self.assertEqual(str(values[0]), "2330") # Symbol
                self.assertIn("加碼觀察", values[1]) # Action

                # Check on_select details population
                advice_frame.table.selection_set(children[0])
                advice_frame.on_select(None)
                root.update()

                details_content = advice_frame.details.get("1.0", "end")
                self.assertIn("總分", details_content)

                # Check warnings box contains skipped 2303
                warnings_content = advice_frame.warnings.get("1.0", "end")
                self.assertIn("2303", warnings_content)
                self.assertIn("無評分資料", warnings_content)

            finally:
                root.destroy()

    def test_saving_factor_scores_preserves_auto_suggested_notes(self):
        """Regression test: save() used to pass {} for notes no matter what
        lookup_symbol() had just suggested, discarding every "why" behind an
        auto-suggested factor (including "insufficient data, kept at
        neutral" disclosures) the moment a person clicked 儲存評分."""
        from factor_score_store import load_symbol_factor_scores

        base = Path("data/test_gui_factor_notes"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        from security_catalog import upsert_from_daily_snapshot
        upsert_from_daily_snapshot(paths["history_database"], [("6182", "合晶")], "TPEx", "2026-07-30T00:00:00")

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths), \
             patch("factor_score_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                score_frame = self._get_tab(notebook, "FactorScoreApp")
                notebook.select(score_frame); root.update()

                score_frame.query.delete(0, "end"); score_frame.query.insert(0, "6182")
                score_frame.lookup_symbol(); score_frame.save()

                _factors, _risk, notes = load_symbol_factor_scores(paths["decision_database"], "6182")
                self.assertTrue(notes, "expected non-empty notes after saving auto-suggested scores")
                self.assertIn("global_risk", notes)
            finally:
                root.destroy()

    def test_manual_backfill_field_rejects_malformed_symbols(self):
        """Regression test: the manual 回補 代號 free-text field was the only
        symbol entry point with no four-digit format check -- a typo or a
        pasted non-code string previously flowed straight into
        _pending_backfill and on into a filename/URL built from it."""
        base = Path("data/test_gui_backfill_validation"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                data_frame = self._get_tab(notebook, "DataManagementFrame")

                data_frame.backfill_symbols.delete(0, "end")
                data_frame.backfill_symbols.insert(0, "2330, ../../evil, 12, abcd")

                symbols = data_frame._backfill_symbols_list()

                self.assertEqual(symbols, ["2330"])
                self.assertIn("../../evil", data_frame.backfill_status["text"])
            finally:
                root.destroy()

    def test_notification_engine_failure_is_logged_not_silently_swallowed(self):
        """Regression test: check_now()'s three scans each used to be a bare
        `except Exception: pass` -- directly contradicting this tab's own
        displayed claim that "事件仍完整記錄於下方清單，不會遺漏" (nothing is
        ever missed). If the scan machinery itself breaks, that must be
        discoverable too."""
        base = Path("data/test_gui_notification_engine_failure"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths), \
             patch("stock_ai_app.check_watchlist_triggers", side_effect=RuntimeError("boom")):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                notification_frame = self._get_tab(notebook, "NotificationCenterFrame")

                notification_frame.check_now()
                root.update()

                records = list_notifications(paths["decision_database"])
                self.assertTrue(any(r.category == "notification_engine_failure" for r in records))
            finally:
                root.destroy()

    def test_selecting_a_notification_shows_its_full_message(self):
        """Regression test: a ttk.Treeview cell never wraps -- it silently
        clips the rendered text to the column width while the full string
        stays in the underlying model. A real same-day partial-update
        failure message ("...更新失敗：The read operation timed out；封存驗
        證通過") was fully present in notification_log but had no way to be
        read from this tab, cut off mid-sentence with no visual indication
        anything was missing."""
        from notification_center import record_notification
        from datetime import datetime as dt

        base = Path("data/test_gui_notification_detail"); base.mkdir(parents=True, exist_ok=True)
        paths = {"history_database": base / "history.sqlite", "decision_database": base / "decision.sqlite", "raw_archive": base / "raw", "backups": base / "backups", "imports": base / "imports"}
        paths["history_database"].unlink(missing_ok=True); paths["decision_database"].unlink(missing_ok=True)

        long_message = "全部日線更新：成功匯入 1092 筆資料；校驗值 19b1c1c957e7…；成功匯入 877 筆資料；校驗值 43e65a0159c4…；更新失敗：The read operation timed out；封存驗證通過"
        record_notification(paths["decision_database"], "data_update", "ALL", long_message, dt.now().astimezone())

        with patch("stock_ai_app.storage_paths", return_value=paths), patch("stock_screener_app.storage_paths", return_value=paths), \
             patch("holdings_manager.storage_paths", return_value=paths), \
             patch("watchlist_app.storage_paths", return_value=paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = StockAiApp(root); root.update_idletasks()
                notebook = next(child for child in app.winfo_children() if child.winfo_class() == "TNotebook")
                notification_frame = self._get_tab(notebook, "NotificationCenterFrame")

                row = notification_frame.table.get_children()[0]
                notification_frame.table.selection_set(row)
                notification_frame.show_detail(None)

                self.assertEqual(notification_frame.detail.get("1.0", "end").strip(), long_message)
            finally:
                root.destroy()
