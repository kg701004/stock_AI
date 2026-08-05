"""Persisted short-stock positions -- lets the short-screening module record what
the owner actually acted on, instead of being a one-shot signal lookup disconnected
from any position tracking. Kept separate from transaction_ledger (which is
long-only and enforces "cannot sell more than held") rather than loosening that
invariant for a rarely-used, higher-risk feature.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from database_utils import database_connection


@dataclass(frozen=True, slots=True)
class ShortPosition:
    id: int | None
    owner: str
    symbol: str
    shares: int
    entry_price: float
    opened_at: datetime
    closed_at: datetime | None
    close_price: float | None
    note: str = ""

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def unrealized_profit(self, current_price: float) -> float:
        """Short profit is entry-minus-current: price falling after shorting is the gain."""
        return round((self.entry_price - current_price) * self.shares, 2)

    def realized_profit(self) -> float | None:
        if self.close_price is None:
            return None
        return round((self.entry_price - self.close_price) * self.shares, 2)


def initialize(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS short_positions (
                id INTEGER PRIMARY KEY, owner TEXT NOT NULL, symbol TEXT NOT NULL,
                shares INTEGER NOT NULL CHECK(shares > 0), entry_price REAL NOT NULL CHECK(entry_price > 0),
                opened_at TEXT NOT NULL, closed_at TEXT, close_price REAL, note TEXT NOT NULL DEFAULT ''
            )
        """)


def open_position(database: Path, owner: str, symbol: str, shares: int, entry_price: float, opened_at: datetime, note: str = "") -> int:
    if not owner.strip() or not symbol.isdigit() or len(symbol) != 4:
        raise ValueError("owner and four-digit symbol are required")
    if shares <= 0 or entry_price <= 0:
        raise ValueError("shares and entry_price must be positive")
    if opened_at.tzinfo is None:
        raise ValueError("opened_at must include a timezone")
    initialize(database)
    with database_connection(database) as connection:
        cursor = connection.execute(
            "INSERT INTO short_positions(owner, symbol, shares, entry_price, opened_at, note) VALUES (?, ?, ?, ?, ?, ?)",
            (owner.strip(), symbol, shares, entry_price, opened_at.isoformat(), note),
        )
    return int(cursor.lastrowid)


def close_position(database: Path, position_id: int, close_price: float, closed_at: datetime) -> None:
    if close_price <= 0:
        raise ValueError("close_price must be positive")
    if closed_at.tzinfo is None:
        raise ValueError("closed_at must include a timezone")
    initialize(database)
    with database_connection(database) as connection:
        row = connection.execute("SELECT closed_at FROM short_positions WHERE id = ?", (position_id,)).fetchone()
        if row is None:
            raise ValueError(f"no short position with id {position_id}")
        if row[0] is not None:
            raise ValueError("short position is already closed")
        connection.execute(
            "UPDATE short_positions SET closed_at = ?, close_price = ? WHERE id = ?",
            (closed_at.isoformat(), close_price, position_id),
        )


def list_positions(database: Path, owner: str | None = None, open_only: bool = False) -> list[ShortPosition]:
    initialize(database)
    query, values = "SELECT id, owner, symbol, shares, entry_price, opened_at, closed_at, close_price, note FROM short_positions", []
    clauses = []
    if owner is not None:
        clauses.append("owner = ?"); values.append(owner)
    if open_only:
        clauses.append("closed_at IS NULL")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY opened_at, id"
    with database_connection(database) as connection:
        rows = connection.execute(query, values).fetchall()
    return [
        ShortPosition(row[0], row[1], row[2], row[3], row[4], datetime.fromisoformat(row[5]), None if row[6] is None else datetime.fromisoformat(row[6]), row[7], row[8])
        for row in rows
    ]
