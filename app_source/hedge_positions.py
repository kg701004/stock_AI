"""Persisted per-owner, per-contract futures hedge position.

Lets the hedge advisory (beta_hedge.suggest_hedge) compare its target contract
count against exposure the owner already holds, instead of suggesting a fresh
full hedge every time regardless of what was already traded.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from beta_hedge import CONTRACT_POINT_VALUES
from database_utils import database_connection


@dataclass(frozen=True, slots=True)
class HedgePosition:
    owner: str
    contract: str
    contracts: float  # signed: negative = short, positive = long
    index_points: float
    updated_at: datetime
    note: str = ""


def initialize(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS hedge_positions (
                owner TEXT NOT NULL, contract TEXT NOT NULL, contracts REAL NOT NULL,
                index_points REAL NOT NULL CHECK(index_points > 0), updated_at TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '', PRIMARY KEY (owner, contract)
            )
        """)


def save_position(database: Path, owner: str, contract: str, contracts: float, index_points: float, updated_at: datetime, note: str = "") -> None:
    if not owner.strip():
        raise ValueError("owner is required")
    if contract not in CONTRACT_POINT_VALUES:
        raise ValueError(f"unknown contract: {contract!r}; expected one of {sorted(CONTRACT_POINT_VALUES)}")
    if index_points <= 0:
        raise ValueError("index_points must be positive")
    if updated_at.tzinfo is None:
        raise ValueError("updated_at must include a timezone")
    initialize(database)
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO hedge_positions(owner, contract, contracts, index_points, updated_at, note) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(owner, contract) DO UPDATE SET contracts = excluded.contracts, index_points = excluded.index_points, "
            "updated_at = excluded.updated_at, note = excluded.note",
            (owner.strip(), contract, contracts, index_points, updated_at.isoformat(), note),
        )


def load_position(database: Path, owner: str, contract: str) -> HedgePosition | None:
    initialize(database)
    with database_connection(database) as connection:
        row = connection.execute(
            "SELECT owner, contract, contracts, index_points, updated_at, note FROM hedge_positions WHERE owner = ? AND contract = ?",
            (owner, contract),
        ).fetchone()
    return None if row is None else HedgePosition(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5])


def clear_position(database: Path, owner: str, contract: str) -> None:
    initialize(database)
    with database_connection(database) as connection:
        connection.execute("DELETE FROM hedge_positions WHERE owner = ? AND contract = ?", (owner, contract))
