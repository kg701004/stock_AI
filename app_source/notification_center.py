"""Proactive notifications: watchlist price triggers and data-update outcomes.

Previously the system was entirely passive -- a user had to open the app and
click into a specific tab to discover a target/stop had been hit, or that a
scheduled update had failed. This module scans for those events and (a)
always records them durably in `notification_log` so the in-app "通知記錄"
list is a complete history, and (b) best-effort fires a native Windows toast
via win11toast so the user learns about it without opening the app.

OS-level delivery is inherently best-effort: a missing/broken toast backend
(non-Windows, WinRT unavailable) must never lose the notification -- the
database log is the source of truth, the toast is a convenience layer on top.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from database_utils import database_connection
from transaction_ledger import initialize as initialize_prices
from watchlist_decision import evaluate
from watchlist_repository import list_items

ACTIONABLE_STATES = {"停利", "停損"}


@dataclass(frozen=True, slots=True)
class NotificationRecord:
    id: int
    category: str
    symbol: str
    message: str
    triggered_at: datetime


def initialize(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS notification_log (
                id INTEGER PRIMARY KEY, category TEXT NOT NULL, symbol TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL, triggered_at TEXT NOT NULL
            )
        """)


def send_os_notification(title: str, message: str) -> bool:
    """Best-effort native toast. Returns False (never raises) if unavailable."""
    try:
        from win11toast import notify
        notify(title, message)
        return True
    except Exception:
        return False


def _recently_logged(database: Path, category: str, symbol: str, message: str, as_of: datetime, within: timedelta) -> bool:
    with database_connection(database) as connection:
        row = connection.execute(
            "SELECT triggered_at FROM notification_log WHERE category = ? AND symbol = ? AND message = ? ORDER BY triggered_at DESC LIMIT 1",
            (category, symbol, message),
        ).fetchone()
    if row is None:
        return False
    last = datetime.fromisoformat(row[0])
    return as_of - last < within


def record_notification(database: Path, category: str, symbol: str, message: str, triggered_at: datetime, notify_os: bool = True, dedupe_within: timedelta = timedelta(hours=20)) -> int | None:
    """Log an event; skip if an identical one was already logged recently (avoids re-notifying every periodic scan)."""
    if not category.strip() or not message.strip() or triggered_at.tzinfo is None:
        raise ValueError("category, message and a timezone-aware triggered_at are required")
    initialize(database)
    if _recently_logged(database, category, symbol, message, triggered_at, dedupe_within):
        return None
    with database_connection(database) as connection:
        cursor = connection.execute(
            "INSERT INTO notification_log(category, symbol, message, triggered_at) VALUES (?, ?, ?, ?)",
            (category, symbol, message, triggered_at.isoformat()),
        )
    if notify_os:
        send_os_notification(f"Stock AI｜{category}", f"{symbol}　{message}" if symbol else message)
    return int(cursor.lastrowid)


def list_notifications(database: Path, limit: int = 100) -> list[NotificationRecord]:
    initialize(database)
    with database_connection(database) as connection:
        rows = connection.execute(
            "SELECT id, category, symbol, message, triggered_at FROM notification_log ORDER BY triggered_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [NotificationRecord(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4])) for row in rows]


def check_watchlist_triggers(decision_database: Path, now: datetime, notify_os: bool = True) -> list[str]:
    """Evaluate every watchlist item against its locked target/stop; log+notify newly actionable hits."""
    initialize_prices(decision_database)
    with database_connection(decision_database) as connection:
        prices = dict(connection.execute("SELECT symbol, price FROM current_prices"))
    fired: list[str] = []
    for item in list_items(decision_database):
        price = prices.get(item.symbol)
        if price is None:
            continue
        action, reason = evaluate(item.reference_price, price, item.target_price, item.stop_price)
        if action not in ACTIONABLE_STATES:
            continue
        message = f"{action}｜{reason}"
        result = record_notification(decision_database, "watchlist_trigger", item.symbol, message, now, notify_os=notify_os)
        if result is not None:
            fired.append(f"{item.symbol} {item.name}：{message}")
    return fired
