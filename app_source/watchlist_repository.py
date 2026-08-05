"""Persistent ownerless watchlist records stored in the local SQLite database."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from database_utils import database_connection


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    id: int
    symbol: str
    name: str
    reference_price: float
    target_price: float
    stop_price: float
    created_at: datetime


def initialize(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_items (
                id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                reference_price REAL NOT NULL CHECK(reference_price > 0),
                target_price REAL NOT NULL CHECK(target_price > 0),
                stop_price REAL NOT NULL CHECK(stop_price > 0), created_at TEXT NOT NULL
            )
        """)


def add_item(database: Path, symbol: str, name: str, reference_price: float, target_price: float, stop_price: float, created_at: datetime) -> int:
    if not symbol.isdigit() or len(symbol) != 4 or not name.strip() or min(reference_price, target_price, stop_price) <= 0 or created_at.tzinfo is None:
        raise ValueError("invalid watchlist item")
    initialize(database)
    with database_connection(database) as connection:
        cursor = connection.execute("INSERT INTO watchlist_items(symbol, name, reference_price, target_price, stop_price, created_at) VALUES (?, ?, ?, ?, ?, ?)", (symbol, name, reference_price, target_price, stop_price, created_at.isoformat()))
    return int(cursor.lastrowid)


def delete_item(database: Path, item_id: int) -> None:
    initialize(database)
    with database_connection(database) as connection:
        connection.execute("DELETE FROM watchlist_items WHERE id = ?", (item_id,))


def update_levels(database: Path, item_id: int, target_price: float, stop_price: float) -> None:
    """Persist program-generated levels; reference price remains user-controlled."""
    if min(target_price, stop_price) <= 0:
        raise ValueError("target and stop must be positive")
    initialize(database)
    with database_connection(database) as connection:
        connection.execute("UPDATE watchlist_items SET target_price = ?, stop_price = ? WHERE id = ?", (target_price, stop_price, item_id))


def list_items(database: Path) -> list[WatchlistItem]:
    initialize(database)
    with database_connection(database) as connection:
        rows = connection.execute("SELECT id, symbol, name, reference_price, target_price, stop_price, created_at FROM watchlist_items ORDER BY symbol").fetchall()
    return [WatchlistItem(row[0], row[1], row[2], row[3], row[4], row[5], datetime.fromisoformat(row[6])) for row in rows]
