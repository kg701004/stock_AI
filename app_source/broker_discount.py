"""Persisted account commission-discount setting (per-owner), used by broker_fees.estimate().

Public牌告費率是給沒有任何折扣的散戶參考用；電子下單實務上幾乎都有折扣（常見 2-6 折），
不記錄折扣會讓損益試算系統性低估實際淨利。
"""
from __future__ import annotations

from pathlib import Path

from database_utils import database_connection

DEFAULT_DISCOUNT = 1.0


def ensure_schema(connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS broker_discounts (owner TEXT PRIMARY KEY, discount REAL NOT NULL)")


def load_discount(database: Path, owner: str) -> float:
    if not database.exists():
        return DEFAULT_DISCOUNT
    with database_connection(database) as connection:
        ensure_schema(connection)
        row = connection.execute("SELECT discount FROM broker_discounts WHERE owner = ?", (owner,)).fetchone()
    return row[0] if row is not None else DEFAULT_DISCOUNT


def save_discount(database: Path, owner: str, discount: float) -> None:
    if not owner.strip():
        raise ValueError("owner must not be blank")
    if not 0 < discount <= 1:
        raise ValueError("discount must be from 0 (exclusive) to 1 (full rate)")
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        ensure_schema(connection)
        connection.execute("INSERT OR REPLACE INTO broker_discounts VALUES (?, ?)", (owner, discount))
