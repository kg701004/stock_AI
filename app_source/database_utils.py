"""Safe SQLite connection context manager."""

from contextlib import contextmanager
import sqlite3
from pathlib import Path
from typing import Iterator


@contextmanager
def database_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a connection that always commits/rolls back and closes.

    timeout=30 makes a connection wait (instead of immediately raising
    "database is locked") when another connection briefly holds a write
    lock -- needed now that historical_backfill.py can run several of these
    concurrently against the same file from a thread pool.
    """
    connection = sqlite3.connect(path, timeout=30)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
