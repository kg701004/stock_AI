"""Tests for the shared safe-connection context manager.

This is the primitive that prevents the Windows file-lock bug found and
fixed 3x in historical_storage.py (raw `sqlite3.connect()` left open keeps a
lock so a later unlink()/reopen fails). It has never had its own dedicated
test before -- every other module's tests exercise it only incidentally.
"""

import unittest
from pathlib import Path

from database_utils import database_connection


class DatabaseConnectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_database_utils.sqlite")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def test_commits_on_success(self) -> None:
        with database_connection(self.database) as connection:
            connection.execute("CREATE TABLE t (value TEXT)")
            connection.execute("INSERT INTO t VALUES ('a')")
        with database_connection(self.database) as connection:
            rows = connection.execute("SELECT value FROM t").fetchall()
        self.assertEqual(rows, [("a",)])

    def test_rolls_back_on_exception(self) -> None:
        with database_connection(self.database) as connection:
            connection.execute("CREATE TABLE t (value TEXT)")
        with self.assertRaises(ValueError):
            with database_connection(self.database) as connection:
                connection.execute("INSERT INTO t VALUES ('should-not-persist')")
                raise ValueError("simulated failure mid-transaction")
        with database_connection(self.database) as connection:
            rows = connection.execute("SELECT value FROM t").fetchall()
        self.assertEqual(rows, [])

    def test_connection_is_closed_even_on_exception(self) -> None:
        # The concrete symptom of the historical bug: an unclosed connection
        # keeps a Windows file lock, so unlink() right after raises
        # PermissionError even though the write itself "succeeded" or failed.
        with database_connection(self.database) as connection:
            connection.execute("CREATE TABLE t (value TEXT)")
        try:
            with database_connection(self.database) as connection:
                connection.execute("INSERT INTO t VALUES ('x')")
                raise RuntimeError("simulated failure")
        except RuntimeError:
            pass
        self.database.unlink()  # must not raise PermissionError

    def test_reraises_the_original_exception_type(self) -> None:
        with database_connection(self.database) as connection:
            connection.execute("CREATE TABLE t (value TEXT)")
        with self.assertRaises(KeyError):
            with database_connection(self.database) as connection:
                raise KeyError("some other real error")

    def test_a_second_connection_waits_instead_of_immediately_erroring_on_a_held_lock(self) -> None:
        """Regression test for concurrent backfill workers writing to the same
        file: without a busy timeout, a second connection hitting a lock held
        by a slow first writer raises sqlite3.OperationalError immediately
        instead of waiting for it to finish."""
        import threading
        import time

        with database_connection(self.database) as connection:
            connection.execute("CREATE TABLE t (value TEXT)")

        release_event = threading.Event()
        holder_ready = threading.Event()

        def hold_write_lock() -> None:
            with database_connection(self.database) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO t VALUES ('holder')")
                holder_ready.set()
                release_event.wait(timeout=5)

        holder_thread = threading.Thread(target=hold_write_lock)
        holder_thread.start()
        holder_ready.wait(timeout=5)
        # Release the lock shortly after the second connection starts waiting
        # on it, from a separate thread -- otherwise nothing would ever
        # unblock the second connection's own wait.
        threading.Timer(0.3, release_event.set).start()
        start = time.monotonic()
        with database_connection(self.database) as connection:
            connection.execute("INSERT INTO t VALUES ('second')")
        elapsed = time.monotonic() - start
        holder_thread.join(timeout=5)
        # It must have actually waited for the lock rather than failing instantly.
        self.assertGreater(elapsed, 0.2)


if __name__ == "__main__":
    unittest.main()
