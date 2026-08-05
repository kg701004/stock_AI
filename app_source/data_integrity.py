"""Post-download integrity verification for locally archived daily bars.

Three independent, real checks -- each catches a different failure mode a
network backfill can produce, and none of them alone would catch everything:

1. Archive integrity (reuses historical_storage.verify_archive): did the raw
   gzip archive behind each import survive unchanged since it was written?
2. OHLC sanity: does every stored bar satisfy low <= open,close <= high, and
   volume >= 0, prices > 0? A malformed bar (a real possibility from a
   parsing edge case or a corrupted response) would silently corrupt every
   downstream moving-average/support-resistance calculation.
3. Trading-day coverage gaps: for each symbol, are there dates within its own
   first-to-last-seen range where OTHER symbols in the same local database
   have a bar but this one doesn't? TWSE and TPEx share one trading-day
   calendar, so the union of every symbol's dates is a real, usable proxy
   calendar -- but it can't distinguish a genuine data gap from a real
   single-stock trading halt, so a flagged gap is a real thing to review,
   not automatically a bug.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from database_utils import database_connection
from historical_storage import verify_archive


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    archive_errors: tuple[str, ...]
    ohlc_violations: tuple[str, ...]
    symbols_with_gaps: tuple[tuple[str, int], ...]  # (symbol, missing_day_count), worst first
    total_bars_checked: int

    @property
    def clean(self) -> bool:
        return not self.archive_errors and not self.ohlc_violations and not self.symbols_with_gaps


def scan_ohlc_sanity(database: Path, symbols: list[str] | None = None) -> list[str]:
    """Real, direct scan of already-stored bars -- catches malformed rows
    regardless of when or how they were imported, not just at import time."""
    if not database.exists():
        return []
    with database_connection(database) as connection:
        query = """
            SELECT symbol, trading_date, open_micros, high_micros, low_micros, close_micros, volume
            FROM daily_bars
            WHERE (low_micros > open_micros OR low_micros > close_micros
                   OR high_micros < open_micros OR high_micros < close_micros
                   OR low_micros > high_micros OR close_micros <= 0 OR open_micros <= 0
                   OR volume < 0)
        """
        params: tuple = ()
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            query += f" AND symbol IN ({placeholders})"
            params = tuple(symbols)
        rows = connection.execute(query, params).fetchall()
    return [
        f"{symbol} {trading_date}: OHLC 不合理 (O={o / 1_000_000:.2f} H={h / 1_000_000:.2f} "
        f"L={l / 1_000_000:.2f} C={c / 1_000_000:.2f} V={v})"
        for symbol, trading_date, o, h, l, c, v in rows
    ]


def scan_trading_day_gaps(database: Path, symbols: list[str] | None = None) -> list[tuple[str, int]]:
    """[(symbol, missing_day_count), ...] for every symbol with at least one
    gap, worst first. A "gap" is a date within the symbol's own observed
    [min, max] range where at least one OTHER locally-stored symbol has a bar
    but this symbol doesn't."""
    if not database.exists():
        return []
    with database_connection(database) as connection:
        symbol_filter = ""
        params: tuple = ()
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            symbol_filter = f"WHERE symbol IN ({placeholders})"
            params = tuple(symbols)
        rows = connection.execute(
            f"""
            SELECT s.symbol, COUNT(*) AS missing_days
            FROM (SELECT DISTINCT trading_date FROM daily_bars) AS d
            CROSS JOIN (
                SELECT symbol, MIN(trading_date) AS first_date, MAX(trading_date) AS last_date
                FROM daily_bars {symbol_filter} GROUP BY symbol
            ) AS s
            WHERE d.trading_date BETWEEN s.first_date AND s.last_date
              AND NOT EXISTS (
                  SELECT 1 FROM daily_bars b WHERE b.symbol = s.symbol AND b.trading_date = d.trading_date
              )
            GROUP BY s.symbol
            ORDER BY missing_days DESC
            """,
            params,
        ).fetchall()
    return [(symbol, count) for symbol, count in rows]


def verify_data_integrity(database: Path, symbols: list[str] | None = None) -> IntegrityReport:
    """Run all three checks and combine them into one report."""
    archive_errors = tuple(verify_archive(database)) if database.exists() else ()
    ohlc_violations = tuple(scan_ohlc_sanity(database, symbols))
    symbols_with_gaps = tuple(scan_trading_day_gaps(database, symbols))
    total_bars = 0
    with database_connection(database) as connection:
        query = "SELECT COUNT(*) FROM daily_bars"
        params: tuple = ()
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            query += f" WHERE symbol IN ({placeholders})"
            params = tuple(symbols)
        try:
            total_bars = connection.execute(query, params).fetchone()[0]
        except sqlite3.OperationalError:
            pass  # daily_bars not created yet (fresh database, nothing imported)
    return IntegrityReport(archive_errors, ohlc_violations, symbols_with_gaps, total_bars)
