"""Per-owner, local settings used by allocation planning."""

from __future__ import annotations

from pathlib import Path

from database_utils import database_connection


def initialize(database: Path) -> None:
    with database_connection(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_preferences (
                owner TEXT PRIMARY KEY,
                cash_balance REAL NOT NULL DEFAULT 0 CHECK(cash_balance >= 0),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def set_cash_balance(database: Path, owner: str, cash_balance: float) -> None:
    if not owner.strip() or cash_balance < 0:
        raise ValueError("owner is required and cash balance cannot be negative")
    initialize(database)
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO portfolio_preferences(owner, cash_balance, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(owner) DO UPDATE SET cash_balance = excluded.cash_balance, updated_at = CURRENT_TIMESTAMP",
            (owner.strip(), cash_balance),
        )


def get_cash_balance(database: Path, owner: str) -> float:
    initialize(database)
    with database_connection(database) as connection:
        row = connection.execute("SELECT cash_balance FROM portfolio_preferences WHERE owner = ?", (owner,)).fetchone()
    return 0.0 if row is None else float(row[0])
