"""Tests for the watchlist add-dialog's code-or-name resolution and
現價 (current price) auto-seeding.

Previously "新增自選股" required both the 4-digit code AND the name to be
typed separately, even though the local securities catalog already knows one
given the other. This mirrors the single "股票代號或名稱" field pattern
already used by holdings_manager.py / short_screening_app.py.

Separately, a freshly-added stock always showed 現價 as "--" even when the
app already had real local daily-bar history for it (from a daily update or
backfill), forcing an unnecessary extra manual "更新現價" step.
"""

import sqlite3
import tkinter as tk
import tkinter.ttk as ttk
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from historical_storage import DailyBar, archive_and_import
from security_catalog import upsert_from_daily_snapshot
from transaction_ledger import set_current_price
from twse_daily_importer import write_normalized_csv
from watchlist_decision import calculate
import watchlist_app
from watchlist_app import resolve_symbol_and_name, seed_current_price_from_history
from watchlist_repository import list_items


class WatchlistAppResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_watchlist_app_catalog.sqlite")
        self.database.unlink(missing_ok=True)
        upsert_from_daily_snapshot(self.database, [("6182", "合晶"), ("2330", "台積電")], "TPEx", "2026-07-30T00:00:00")

    def tearDown(self) -> None:
        self.database.unlink(missing_ok=True)

    def test_code_only_resolves_the_catalogued_name(self) -> None:
        symbol, name = resolve_symbol_and_name(self.database, "6182")
        self.assertEqual(symbol, "6182")
        self.assertEqual(name, "合晶")

    def test_name_only_resolves_the_code(self) -> None:
        symbol, name = resolve_symbol_and_name(self.database, "台積電")
        self.assertEqual(symbol, "2330")
        self.assertEqual(name, "台積電")

    def test_unresolvable_query_raises_a_clear_error(self) -> None:
        with self.assertRaises(ValueError):
            resolve_symbol_and_name(self.database, "9999")


def _read_prices(database: Path) -> dict[str, float]:
    connection = sqlite3.connect(database)
    try:
        return dict(connection.execute("SELECT symbol, price FROM current_prices"))
    finally:
        connection.close()


class SeedCurrentPriceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decision_database = Path("data/test_watchlist_app_decisions.sqlite")
        self.history_database = Path("data/test_watchlist_app_history.sqlite")
        self.decision_database.unlink(missing_ok=True)
        self.history_database.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.decision_database.unlink(missing_ok=True)
        self.history_database.unlink(missing_ok=True)

    def test_seeds_price_from_the_latest_real_daily_bar(self) -> None:
        published_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        bars = [
            DailyBar("6182", date(2026, 5, 29), 40.0, 41.0, 39.0, 40.5, 1000, "TEST", published_at),
            DailyBar("6182", date(2026, 5, 30), 41.0, 42.0, 40.0, 41.5, 1000, "TEST", published_at),
        ]
        csv_path = Path("data/test_watchlist_seed_bars.csv")
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, self.history_database, Path("data/test_watchlist_seed_archive"))

        result = seed_current_price_from_history(self.decision_database, self.history_database, "6182")
        self.assertAlmostEqual(result, 41.5)  # the later (5/30) close, not the first row
        self.assertAlmostEqual(_read_prices(self.decision_database)["6182"], 41.5)

    def test_does_not_overwrite_an_existing_price(self) -> None:
        set_current_price(self.decision_database, "6182", 999.0, datetime.now().astimezone())
        published_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        bars = [DailyBar("6182", date(2026, 5, 30), 41.0, 42.0, 40.0, 41.5, 1000, "TEST", published_at)]
        csv_path = Path("data/test_watchlist_seed_bars2.csv")
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, self.history_database, Path("data/test_watchlist_seed_archive2"))

        result = seed_current_price_from_history(self.decision_database, self.history_database, "6182")
        self.assertIsNone(result)
        self.assertEqual(_read_prices(self.decision_database)["6182"], 999.0)

    def test_returns_none_when_no_history_exists_yet(self) -> None:
        result = seed_current_price_from_history(self.decision_database, self.history_database, "6182")
        self.assertIsNone(result)


class AddDialogInitialDecisionTests(unittest.TestCase):
    """Reproduces a real user report: adding a stock with a low reference
    price (e.g. an old cost basis) while today's real seeded price is much
    higher used to flash a misleading "停利" (target already hit) the moment
    it was added, because add_item() leaves target=stop=reference_price as a
    raw placeholder -- any real price above that low placeholder reads as
    "already blown past target". It then "fixed itself" back to 續抱／觀察
    the instant 重新分析全部 recomputed real levels from the actual price --
    which looked like the judgement was unstable/wrong rather than the
    initial placeholder simply never having been analyzed yet."""

    def setUp(self) -> None:
        self.base = Path("data/test_watchlist_initial_decision")
        self.base.mkdir(parents=True, exist_ok=True)
        self.paths = {
            "history_database": self.base / "history.sqlite",
            "decision_database": self.base / "decision.sqlite",
            "raw_archive": self.base / "raw", "backups": self.base / "backups", "imports": self.base / "imports",
        }
        self.paths["history_database"].unlink(missing_ok=True)
        self.paths["decision_database"].unlink(missing_ok=True)
        upsert_from_daily_snapshot(self.paths["history_database"], [("6182", "合晶")], "TPEx", "2026-07-30T00:00:00")
        bars = [DailyBar("6182", date(2026, 7, 30), 84.0, 85.0, 83.0, 84.7, 20_000_000, "TEST", datetime(2026, 7, 30, tzinfo=timezone.utc))]
        csv_path = self.paths["imports"]; csv_path.mkdir(parents=True, exist_ok=True); csv_path = csv_path / "seed.csv"
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, self.paths["history_database"], self.paths["raw_archive"])

    def test_first_render_after_add_is_not_a_false_take_profit_signal(self) -> None:
        with patch("watchlist_app.storage_paths", return_value=self.paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = watchlist_app.WatchlistApp(root)
                app.add()
                dialog = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
                form = dialog.winfo_children()[0]
                symbol_entry, reference_entry = [w for w in form.winfo_children() if isinstance(w, ttk.Entry)]
                symbol_entry.delete(0, "end"); symbol_entry.insert(0, "6182")
                reference_entry.delete(0, "end"); reference_entry.insert(0, "42")  # far below today's real 84.7 close
                buttons_frame = [w for w in form.winfo_children() if isinstance(w, ttk.Frame)][-1]
                next(b for b in buttons_frame.winfo_children() if b["text"] == "確認新增").invoke()
                root.update()

                app.refresh()
                row = app.table.item(app.table.get_children()[0])["values"]
                item = list_items(self.paths["decision_database"])[0]

                # The real formula (same one 重新分析全部 uses) must already
                # be reflected -- target/stop must NOT still equal the raw
                # reference_price placeholder.
                self.assertNotEqual(item.target_price, 42.0)
                self.assertNotEqual(item.stop_price, 42.0)
                self.assertNotEqual(row[8], "停利", f"first render should not show a false take-profit signal, got row={row!r}")
            finally:
                root.destroy()


class TooltipPositioningTests(unittest.TestCase):
    """Regression test for a real user report: hovering the "判斷" column
    (the rightmost column) placed the explanation tooltip partly past the
    screen edge, silently clipping the text -- an override-redirect Toplevel
    is never repositioned by the window manager to stay on-screen, unlike a
    normal window."""

    def setUp(self) -> None:
        self.base = Path("data/test_watchlist_tooltip")
        self.base.mkdir(parents=True, exist_ok=True)
        self.paths = {
            "history_database": self.base / "history.sqlite",
            "decision_database": self.base / "decision.sqlite",
            "raw_archive": self.base / "raw", "backups": self.base / "backups", "imports": self.base / "imports",
        }
        self.paths["history_database"].unlink(missing_ok=True)
        self.paths["decision_database"].unlink(missing_ok=True)
        upsert_from_daily_snapshot(self.paths["history_database"], [("6182", "合晶")], "TPEx", "2026-07-30T00:00:00")
        bars = [DailyBar("6182", date(2026, 7, 30), 84.0, 85.0, 83.0, 84.7, 20_000_000, "TEST", datetime(2026, 7, 30, tzinfo=timezone.utc))]
        csv_path = self.paths["imports"]; csv_path.mkdir(parents=True, exist_ok=True); csv_path = csv_path / "seed.csv"
        write_normalized_csv(bars, csv_path)
        archive_and_import(csv_path, self.paths["history_database"], self.paths["raw_archive"])

    def test_tooltip_flips_to_stay_within_the_screen_when_near_the_edge(self) -> None:
        with patch("watchlist_app.storage_paths", return_value=self.paths):
            root = tk.Tk(); root.withdraw()
            try:
                app = watchlist_app.WatchlistApp(root)
                from watchlist_repository import add_item
                add_item(self.paths["decision_database"], "6182", "合晶", 42.0, 42.0, 42.0, datetime.now().astimezone())
                app.refresh()
                row = app.table.get_children()[0]
                item_id = int(app.table.item(row)["values"][0])
                # A long detail string, matching the real length of an
                # actual explanation (target/stop/risk/ATR/support/
                # resistance/technical confirmation all concatenated).
                app.details[item_id] = "現價跌破有效停損 1629.50。分數 51.2、風險分數 30.0。ATR 1425.00、壓力 1945.00、相對強勢。技術確認：技術偏多 68.0 分。柱體為正，短期動能偏強。" * 2

                screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
                app.show_tip(row, screen_width - 5, screen_height - 5)  # cursor pinned at the bottom-right corner
                root.update_idletasks()

                tip_x, tip_y = app.tooltip.winfo_x(), app.tooltip.winfo_y()
                tip_width, tip_height = app.tooltip.winfo_width(), app.tooltip.winfo_height()
                self.assertLessEqual(tip_x + tip_width, screen_width, "tooltip must not extend past the right edge of the screen")
                self.assertLessEqual(tip_y + tip_height, screen_height, "tooltip must not extend past the bottom edge of the screen")
                self.assertGreaterEqual(tip_x, 0)
                self.assertGreaterEqual(tip_y, 0)
            finally:
                app.hide_tip()
                root.destroy()


if __name__ == "__main__":
    unittest.main()
