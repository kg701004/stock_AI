"""Compact, auditable storage for long-lived end-of-day market history.

Raw imports are archived as UTF-8 CSV compressed with gzip plus SHA-256.  The
same rows are stored in SQLite using integer micro-units for prices, avoiding
floating-point drift while keeping the query database small and portable.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from database_utils import database_connection

# The single canonical daily_bars schema. Previously latest_close_price() and
# average_daily_trading_value() each had their own truncated "CREATE TABLE IF
# NOT EXISTS daily_bars (...)" with fewer columns -- since IF NOT EXISTS is a
# no-op once ANY of the three has created the table, whichever of these ran
# first (e.g. opening the 持股 or 自選 tab before ever running a backfill)
# would permanently leave the truncated schema in place, and every later
# archive_and_import() insert (which needs the full column set) would fail.
# All three call sites now share this exact statement so it doesn't matter
# which one runs first.
_DAILY_BARS_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS imports (
        checksum TEXT PRIMARY KEY, original_name TEXT NOT NULL, archive_path TEXT NOT NULL,
        imported_at TEXT NOT NULL, row_count INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS daily_bars (
        symbol TEXT NOT NULL, trading_date TEXT NOT NULL,
        open_micros INTEGER NOT NULL, high_micros INTEGER NOT NULL,
        low_micros INTEGER NOT NULL, close_micros INTEGER NOT NULL,
        volume INTEGER NOT NULL, source TEXT NOT NULL, published_at TEXT NOT NULL,
        import_checksum TEXT NOT NULL,
        PRIMARY KEY(symbol, trading_date, source),
        FOREIGN KEY(import_checksum) REFERENCES imports(checksum)
    );
    CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(trading_date);
"""


@dataclass(frozen=True, slots=True)
class DailyBar:
    symbol: str
    trading_date: date
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    source: str
    published_at: datetime

    def __post_init__(self) -> None:
        if not self.symbol.isdigit() or len(self.symbol) != 4:
            raise ValueError("symbol must be a four-digit Taiwan stock code")
        if min(self.open_price, self.high_price, self.low_price, self.close_price) <= 0:
            raise ValueError("prices must be positive")
        if self.high_price < max(self.open_price, self.close_price) or self.low_price > min(self.open_price, self.close_price):
            raise ValueError("OHLC values are inconsistent")
        if self.volume < 0 or not self.source.strip():
            raise ValueError("volume must be non-negative and source cannot be blank")


REQUIRED_COLUMNS = {"symbol", "date", "open", "high", "low", "close", "volume", "source", "published_at"}


def _price_to_micros(price: float) -> int:
    return round(price * 1_000_000)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _archive_reference(database: Path, archive_path: Path) -> str:
    """Return a portable archive reference when database and archive share a root."""
    try:
        return str(archive_path.resolve().relative_to(database.parent.resolve()))
    except ValueError:
        return str(archive_path.resolve())


def _resolve_archive_path(database: Path, stored_path: str) -> Path:
    """Resolve current and legacy archive references.

    Args:
        database: SQLite history database containing the import record.
        stored_path: Relative portable path or legacy absolute path.

    Returns:
        The best matching archive path. If no file exists, the original
        resolved candidate is returned so validation reports it clearly.
    """
    path = Path(stored_path)
    candidate = path if path.is_absolute() else database.parent / path
    portable_root = database.parent / "raw_archive"
    matches = list(portable_root.rglob(path.name)) if portable_root.exists() else []
    if len(matches) == 1:
        return matches[0]
    return candidate


def make_archive_paths_portable(database: Path) -> int:
    """Rewrite resolvable legacy archive paths relative to the database.

    Args:
        database: History database that travels with ``raw_archive``.

    Returns:
        Number of import records changed.
    """
    changed = 0
    with database_connection(database) as connection:
        rows = connection.execute("SELECT checksum, archive_path FROM imports").fetchall()
        for checksum, stored_path in rows:
            resolved = _resolve_archive_path(database, stored_path)
            if not resolved.exists():
                continue
            portable = _archive_reference(database, resolved)
            if portable != stored_path:
                connection.execute(
                    "UPDATE imports SET archive_path = ? WHERE checksum = ?",
                    (portable, checksum),
                )
                changed += 1
    return changed


def read_daily_csv(path: Path) -> list[DailyBar]:
    """Read normalized daily bars; timestamps must include timezone information."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not REQUIRED_COLUMNS <= set(reader.fieldnames):
            raise ValueError(f"CSV is missing: {sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))}")
        bars: list[DailyBar] = []
        for row_number, row in enumerate(reader, start=2):
            try:
                published_at = datetime.fromisoformat(row["published_at"])
                if published_at.tzinfo is None:
                    raise ValueError("published_at needs a timezone")
                bars.append(DailyBar(row["symbol"], date.fromisoformat(row["date"]), float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), int(row["volume"]), row["source"], published_at))
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid row {row_number}: {error}") from error
    if not bars:
        raise ValueError("CSV contains no daily bars")
    return bars


def latest_close_price(database: Path, symbol: str) -> tuple[float, date] | None:
    """Return (close_price, trading_date) for the most recent locally archived
    daily bar, or None if this symbol has no history yet. Deliberately the raw
    (unadjusted) close -- this answers "what did it actually last trade at",
    not the ex-dividend-adjusted series technical_factor.py uses for signals."""
    if not database.exists():
        return None
    with database_connection(database) as connection:
        connection.executescript(_DAILY_BARS_SCHEMA_SQL)
        row = connection.execute(
            "SELECT trading_date, close_micros FROM daily_bars WHERE symbol = ? ORDER BY trading_date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    return None if row is None else (row[1] / 1_000_000, date.fromisoformat(row[0]))


def average_daily_trading_value(database: Path, symbol: str, window: int = 20) -> float | None:
    """Return the average daily trading value (NT$, close * volume) over the
    most recent `window` locally archived bars, or None if there's no local
    history yet. A standard, simple proxy for "how liquid/tradable is this
    stock", independent of today's price level (unlike volume alone)."""
    if not database.exists():
        return None
    with database_connection(database) as connection:
        connection.executescript(_DAILY_BARS_SCHEMA_SQL)
        rows = connection.execute(
            "SELECT close_micros, volume FROM daily_bars WHERE symbol = ? ORDER BY trading_date DESC LIMIT ?",
            (symbol, window),
        ).fetchall()
    if not rows:
        return None
    values = [(close_micros / 1_000_000) * volume for close_micros, volume in rows]
    return sum(values) / len(values)


def ensure_wal_mode(database: Path) -> None:
    """Enable WAL journal mode if it isn't already active.

    journal_mode is a database-level setting that persists in the file
    itself once WAL is enabled -- re-issuing "PRAGMA journal_mode=WAL" on
    every call is not just redundant, it's a real, confirmed source of
    intermittent "database is locked" errors: several concurrent backfill
    workers each independently trying the switch on their first write to a
    fresh (not-yet-WAL) file raced each other, since changing journal mode
    briefly needs a stronger lock than an ordinary read/write and multiple
    threads doing "check current mode, then maybe set it" is not itself
    atomic. Calling this ONCE, single-threaded, before any concurrent access
    starts (see historical_backfill.run_backfill) avoids that race entirely;
    archive_and_import's own per-call check-then-skip is a lighter-weight
    fallback for callers that never call this explicitly.
    """
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            connection.execute("PRAGMA journal_mode=WAL")


def archive_and_import(csv_path: Path, database: Path, archive_root: Path) -> tuple[str, int]:
    """Archive raw CSV once and import its bars idempotently into SQLite."""
    # Local import: data_quality imports DailyBar from this module, so a
    # module-level import here would be circular.
    from data_quality import validate_daily_bars
    checksum = _sha256(csv_path)
    bars = read_daily_csv(csv_path)
    quality = validate_daily_bars(bars)
    if not quality.accepted:
        # Reject outright rather than let INSERT OR REPLACE silently collapse
        # duplicate (symbol, trading_date, source) rows while still reporting
        # the original, now-inaccurate row count as a success.
        raise ValueError("daily bars failed quality validation: " + "; ".join(quality.errors))
    archive_path = archive_root / f"{bars[0].trading_date.year:04d}" / f"{bars[0].trading_date.month:02d}" / f"{checksum}.csv.gz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if not archive_path.exists():
        # Write to a temp file and rename into place atomically: if the
        # process is killed mid-write (crash, force-quit, power loss), the
        # partial bytes land at the .tmp path, never at archive_path itself
        # -- otherwise a later retry with the identical checksum would see
        # archive_path already "exists" and skip rewriting it, permanently
        # leaving a truncated archive that only verify_archive's checksum
        # check would ever catch, and only after the fact.
        temp_path = archive_path.with_suffix(archive_path.suffix + ".tmp")
        with csv_path.open("rb") as source, gzip.open(temp_path, "wb", compresslevel=9) as destination:
            shutil.copyfileobj(source, destination)
        os.replace(temp_path, archive_path)
    with database_connection(database) as connection:
        # journal_mode is a database-level setting that persists in the file
        # itself once WAL is enabled -- re-issuing "PRAGMA journal_mode=WAL"
        # on every single call is redundant after the first time, and a real,
        # confirmed source of intermittent "database is locked" errors under
        # concurrent access (changing journal mode briefly needs a stronger
        # lock than a normal read/write, one that does not reliably respect
        # connection timeout= the way ordinary statements do). Only ever
        # issue the SET once, gated behind a plain read of the current mode.
        if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(_DAILY_BARS_SCHEMA_SQL)
        already_imported = connection.execute("SELECT 1 FROM imports WHERE checksum = ?", (checksum,)).fetchone()
        if already_imported:
            return checksum, 0
        connection.execute(
            "INSERT INTO imports VALUES (?, ?, ?, ?, ?)",
            (
                checksum,
                csv_path.name,
                _archive_reference(database, archive_path),
                datetime.now().astimezone().isoformat(),
                len(bars),
            ),
        )
        connection.executemany(
            "INSERT OR REPLACE INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(bar.symbol, bar.trading_date.isoformat(), _price_to_micros(bar.open_price), _price_to_micros(bar.high_price), _price_to_micros(bar.low_price), _price_to_micros(bar.close_price), bar.volume, bar.source, bar.published_at.isoformat(), checksum) for bar in bars],
        )
    return checksum, len(bars)


def verify_archive(database: Path) -> list[str]:
    """Return integrity errors for archived raw files and their recorded hashes."""
    errors: list[str] = []
    with database_connection(database) as connection:
        # A brand-new database (nothing imported yet) has no `imports` table.
        # Ensure it exists so a fresh install doesn't crash on startup here.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS imports (
                checksum TEXT PRIMARY KEY, original_name TEXT NOT NULL, archive_path TEXT NOT NULL,
                imported_at TEXT NOT NULL, row_count INTEGER NOT NULL
            )
        """)
        for checksum, archive_path in connection.execute("SELECT checksum, archive_path FROM imports"):
            path = _resolve_archive_path(database, archive_path)
            if not path.exists():
                errors.append(f"missing archive: {path}")
                continue
            digest = hashlib.sha256()
            with gzip.open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != checksum:
                errors.append(f"checksum mismatch: {path}")
    return errors


def backup_database(database: Path, destination: Path) -> None:
    """Create a consistent SQLite backup without copying a live WAL file directly."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as source, database_connection(destination) as backup:
        source.backup(backup)
