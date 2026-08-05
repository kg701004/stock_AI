"""Tests for compact raw archive and SQLite historical storage."""

import gzip
import shutil
import sqlite3
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from historical_storage import (
    DailyBar,
    _resolve_archive_path,
    archive_and_import,
    average_daily_trading_value,
    ensure_wal_mode,
    latest_close_price,
    make_archive_paths_portable,
    verify_archive,
)
from twse_daily_importer import write_normalized_csv


class HistoricalStorageTests(unittest.TestCase):
    def test_import_is_idempotent_and_archive_verifies(self) -> None:
        database = Path("data/test_history.sqlite")
        archive = Path("data/test_raw_archive")
        checksum, count = archive_and_import(Path("data/sample_daily_bars.csv"), database, archive)
        _, repeated_count = archive_and_import(Path("data/sample_daily_bars.csv"), database, archive)
        self.assertEqual(len(checksum), 64)
        self.assertIn(count, {0, 2})
        self.assertEqual(repeated_count, 0)
        self.assertEqual(verify_archive(database), [])
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0], 2)

    def test_calling_latest_close_price_before_any_import_does_not_break_later_archive_and_import(self) -> None:
        """Regression test: latest_close_price() and average_daily_trading_value()
        used to each CREATE TABLE their own truncated daily_bars schema (fewer
        columns than archive_and_import's real one). Since "IF NOT EXISTS" is a
        no-op once any of the three has created the table, calling either of
        these BEFORE the first real backfill/import (e.g. opening the 持股 or
        自選 tab on a fresh database) would permanently leave the truncated
        schema in place and break every subsequent archive_and_import insert."""
        database = Path("data/test_schema_order.sqlite")
        archive = Path("data/test_schema_order_archive")
        database.unlink(missing_ok=True)
        shutil.rmtree(archive, ignore_errors=True)
        database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database):
            pass  # the database FILE exists, but has no tables yet

        self.assertIsNone(latest_close_price(database, "2330"))
        self.assertIsNone(average_daily_trading_value(database, "2330"))

        checksum, count = archive_and_import(Path("data/sample_daily_bars.csv"), database, archive)
        self.assertEqual(len(checksum), 64)
        self.assertEqual(count, 2)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0], 2)

    def test_bad_quality_import_is_rejected_and_writes_nothing(self) -> None:
        """A CSV that fails the data_quality gate (here: two rows for the
        same symbol/date/source) must be rejected before anything is
        written -- no archive copy, no database rows -- so a bad import
        never leaves the store in a half-updated state."""
        database = Path("data/test_history_rejected.sqlite")
        archive = Path("data/test_raw_archive_rejected")
        database.unlink(missing_ok=True)
        csv_path = Path("data/test_duplicate_rows.csv")
        published_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        duplicate_bars = [
            DailyBar("2330", date(2026, 6, 1), 10.0, 11.0, 9.0, 10.5, 1000, "TEST", published_at),
            DailyBar("2330", date(2026, 6, 1), 10.0, 11.0, 9.0, 10.5, 2000, "TEST", published_at),
        ]
        write_normalized_csv(duplicate_bars, csv_path)
        with self.assertRaises(ValueError) as context:
            archive_and_import(csv_path, database, archive)
        self.assertIn("quality validation", str(context.exception))
        self.assertFalse(database.exists(), "rejected import must not create/touch the database at all")
        self.assertEqual(list(archive.rglob("*.csv.gz")) if archive.exists() else [], [])

    def test_interrupted_archive_write_never_leaves_a_truncated_file_at_the_final_path(self) -> None:
        """Regression test for a real crash-safety gap: if the process is
        killed (force-quit, power loss) while gzip-compressing the archive
        file, the OLD code wrote directly to the final archive_path -- a
        truncated file would be left there, and since archive_and_import's
        "if not archive_path.exists()" check treats any existing file as
        already-correctly-archived, a later retry with the identical
        checksum would skip rewriting it, permanently leaving corruption
        that only verify_archive's checksum check would catch, after the
        fact. Writing to a .tmp path and os.replace()-ing into place means
        an interruption mid-write leaves only an inert .tmp file behind --
        archive_path itself never exists in a half-written state."""
        root = Path("data/test_interrupted_archive_write")
        shutil.rmtree(root, ignore_errors=True)
        database = root / "history.sqlite"
        archive = root / "raw_archive"

        from unittest.mock import patch
        with patch("historical_storage.shutil.copyfileobj", side_effect=OSError("simulated crash mid-write")):
            with self.assertRaises(OSError):
                archive_and_import(Path("data/sample_daily_bars.csv"), database, archive)

        gz_files = list(archive.rglob("*.csv.gz")) if archive.exists() else []
        self.assertEqual(gz_files, [], "no truncated file must exist at the final archive path after a simulated crash")
        tmp_files = list(archive.rglob("*.tmp")) if archive.exists() else []
        self.assertEqual(len(tmp_files), 1, "the incomplete write should have landed at a .tmp path instead")

        # A real retry afterwards must succeed cleanly and produce a real,
        # verifiable archive -- not be blocked by the leftover .tmp file.
        checksum, count = archive_and_import(Path("data/sample_daily_bars.csv"), database, archive)
        self.assertEqual(count, 2)
        self.assertEqual(verify_archive(database), [])

    def test_ensure_wal_mode_enables_wal_and_is_idempotent(self) -> None:
        """Regression test for a real, confirmed root cause of an
        intermittent "database is locked" error under concurrent backfill
        workers: archive_and_import used to re-issue "PRAGMA journal_mode=WAL"
        on every single call, and several worker threads racing that switch
        on a fresh (not-yet-WAL) file could transiently fail even with a
        busy_timeout set, since changing journal mode briefly needs a
        stronger lock than an ordinary statement. ensure_wal_mode is meant
        to be called once, single-threaded, before concurrent access starts."""
        database = Path("data/test_ensure_wal_mode.sqlite")
        database.unlink(missing_ok=True)
        ensure_wal_mode(database)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        ensure_wal_mode(database)  # calling again on an already-WAL database must not raise or misbehave
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")

    def test_verify_archive_detects_a_tampered_file(self) -> None:
        """A corrupted/tampered archive file (bit rot, manual edit, bad
        backup restore) must be caught by checksum verification, not
        silently trusted.

        Uses its own isolated directory: _resolve_archive_path() searches
        `database.parent / "raw_archive"` by filename as a portability
        fallback, so sharing `data/` with other tests that archive the same
        byte-identical sample CSV (same SHA-256 -> same archive filename)
        would let this test's tampering corrupt a different test's file.
        """
        root = Path("data/test_tamper_isolated")
        shutil.rmtree(root, ignore_errors=True)
        database = root / "history.sqlite"
        archive = root / "raw_archive"
        checksum, _ = archive_and_import(Path("data/sample_daily_bars.csv"), database, archive)
        self.assertEqual(verify_archive(database), [])
        with sqlite3.connect(database) as connection:
            archive_path_str = connection.execute("SELECT archive_path FROM imports WHERE checksum = ?", (checksum,)).fetchone()[0]
        archive_path = _resolve_archive_path(database, archive_path_str)
        with gzip.open(archive_path, "wb") as handle:
            handle.write(b"tampered content that will not match the recorded checksum")
        errors = verify_archive(database)
        self.assertEqual(len(errors), 1)
        self.assertIn("checksum mismatch", errors[0])
        shutil.rmtree(root, ignore_errors=True)  # never leave a tampered file behind for other tests/runs to find

    def test_latest_close_price_returns_the_most_recent_real_bar(self) -> None:
        database = Path("data/test_history.sqlite")
        archive_and_import(Path("data/sample_daily_bars.csv"), database, Path("data/test_raw_archive"))
        with sqlite3.connect(database) as connection:
            symbol, trading_date_str, close_micros = connection.execute(
                "SELECT symbol, trading_date, close_micros FROM daily_bars ORDER BY trading_date DESC LIMIT 1"
            ).fetchone()
        result = latest_close_price(database, symbol)
        self.assertIsNotNone(result)
        close, trading_date = result
        self.assertAlmostEqual(close, close_micros / 1_000_000)
        self.assertEqual(trading_date, date.fromisoformat(trading_date_str))

    def test_latest_close_price_is_none_for_unknown_symbol_or_missing_database(self) -> None:
        database = Path("data/test_history.sqlite")
        archive_and_import(Path("data/sample_daily_bars.csv"), database, Path("data/test_raw_archive"))
        self.assertIsNone(latest_close_price(database, "0000"))
        self.assertIsNone(latest_close_price(Path("data/test_history_never_created.sqlite"), "2330"))

    def test_average_daily_trading_value_matches_a_hand_computed_real_import(self) -> None:
        database = Path("data/test_history.sqlite")
        archive_and_import(Path("data/sample_daily_bars.csv"), database, Path("data/test_raw_archive"))
        with sqlite3.connect(database) as connection:
            symbol = connection.execute("SELECT symbol FROM daily_bars LIMIT 1").fetchone()[0]
            rows = connection.execute("SELECT close_micros, volume FROM daily_bars WHERE symbol=?", (symbol,)).fetchall()
        expected = sum((close / 1_000_000) * volume for close, volume in rows) / len(rows)
        self.assertAlmostEqual(average_daily_trading_value(database, symbol, window=20), expected)
        self.assertIsNone(average_daily_trading_value(database, "0000"))
        self.assertIsNone(average_daily_trading_value(Path("data/test_history_never_created.sqlite"), symbol))

    def test_legacy_absolute_archive_can_move_with_database(self) -> None:
        database_dir = Path("data/test_legacy_move_isolated")
        shutil.rmtree(database_dir, ignore_errors=True)
        database_dir.mkdir(parents=True, exist_ok=True)
        database = database_dir / "history.sqlite"

        src_archive = Path("data/test_raw_archive")
        dest_archive = database_dir / "raw_archive"
        if src_archive.exists():
            shutil.copytree(src_archive, dest_archive)

        archive_and_import(Path("data/sample_daily_bars.csv"), database, dest_archive)

        moved = _resolve_archive_path(
            database,
            "Z:/old-computer/raw_archive/2026/07/"
            "78163eeab33bbb5948a1d98b22d33ac8c679a5d5ed23c9b24bcf67331685d731.csv.gz",
        )
        self.assertEqual(moved.name, "78163eeab33bbb5948a1d98b22d33ac8c679a5d5ed23c9b24bcf67331685d731.csv.gz")
        self.assertTrue(moved.exists())
        self.assertGreaterEqual(make_archive_paths_portable(database), 0)
        shutil.rmtree(database_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
