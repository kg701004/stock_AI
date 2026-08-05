"""Auditable local journal for reviewing why a decision was made."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from database_utils import database_connection

@dataclass(frozen=True, slots=True)
class JournalEntry:
    symbol: str; action: str; score: float; reason: str; created_at: datetime

def initialize(database: Path) -> None:
    with database_connection(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS decision_journal (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, action TEXT NOT NULL, score REAL NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)")

def add_entry(database: Path, entry: JournalEntry) -> int:
    if not entry.symbol.isdigit() or len(entry.symbol) != 4 or not entry.action.strip() or entry.created_at.tzinfo is None: raise ValueError("invalid journal entry")
    initialize(database)
    with database_connection(database) as connection:
        cursor = connection.execute("INSERT INTO decision_journal(symbol, action, score, reason, created_at) VALUES (?, ?, ?, ?, ?)", (entry.symbol, entry.action, entry.score, entry.reason, entry.created_at.isoformat()))
        value = int(cursor.lastrowid); cursor.close(); return value

def list_entries(database: Path) -> list[JournalEntry]:
    initialize(database)
    with database_connection(database) as connection:
        cursor = connection.execute("SELECT symbol, action, score, reason, created_at FROM decision_journal ORDER BY created_at DESC, id DESC"); rows = cursor.fetchall(); cursor.close()
    return [JournalEntry(*row[:4], datetime.fromisoformat(row[4])) for row in rows]
