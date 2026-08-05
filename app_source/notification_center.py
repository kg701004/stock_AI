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


def _has_drift_notification_today(database: Path, symbol: str, now: datetime) -> bool:
    initialize(database)
    with database_connection(database) as connection:
        rows = connection.execute(
            "SELECT triggered_at FROM notification_log WHERE category = 'allocation_drift' AND symbol = ?",
            (symbol,),
        ).fetchall()
    for row in rows:
        dt = datetime.fromisoformat(row[0])
        if dt.date() == now.date():
            return True
    return False


def check_allocation_drift(
    decision_database: Path,
    history_database: Path,
    threshold_pct: float = 5.0,
    now: datetime | None = None,
    notify_os: bool = True,
) -> list[str]:
    """Evaluate owner portfolios for weight drift and log notifications if they exceed the threshold."""
    if now is None:
        now = datetime.now().astimezone()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    # Local imports to avoid circular dependencies
    from transaction_ledger import calculate_holdings
    from portfolio_preferences import get_cash_balance
    from weighted_analysis import assess_stock, load_weight_config
    from factor_score_store import load_all_current_assessments
    from watchlist_repository import list_items
    from security_catalog import load_security_metadata
    from portfolio_allocation import build_allocation_plan, load_allocation_rules

    # Ensure config files can be found
    config_path = Path("config/analysis_weights.json")
    if not config_path.exists():
        config_path = Path(__file__).parent / "config" / "analysis_weights.json"

    rules_path = Path("config/allocation_rules.json")
    if not rules_path.exists():
        rules_path = Path(__file__).parent / "config" / "allocation_rules.json"

    ledger = calculate_holdings(decision_database)
    owners = sorted({item.owner for item in ledger if item.market_value is not None})
    if not owners:
        return []

    weight_config = load_weight_config(config_path)
    scores = {
        symbol: assess_stock(row, weight_config).final_score
        for symbol, row in load_all_current_assessments(decision_database, history_database).items()
    }
    watchlist_symbols = [item.symbol for item in list_items(decision_database)]
    rules = load_allocation_rules(rules_path)

    fired: list[str] = []

    for owner in owners:
        cash_balance = get_cash_balance(decision_database, owner)
        plan = build_allocation_plan(
            owner,
            ledger,
            scores,
            load_security_metadata(history_database, symbols={item.symbol for item in ledger} | set(watchlist_symbols)),
            rules,
            watchlist_symbols,
            cash_balance=cash_balance,
        )

        # Only check actually owned symbols (shares > 0 and market_value is not None)
        owned_symbols = {
            item.symbol for item in ledger
            if item.owner == owner and item.shares > 0 and item.market_value is not None
        }

        for suggestion in plan.suggestions:
            if suggestion.symbol not in owned_symbols:
                continue
            if suggestion.symbol not in scores:
                # No factor score on record for this holding -- build_allocation_plan
                # defaults its target weight to 0% in this case, which would look
                # like a large "drift" that isn't real; skip until it's been scored.
                continue

            drift = abs(suggestion.current_weight_pct - suggestion.target_weight_pct)
            if drift > threshold_pct:
                # Deduplication: check if already notified today
                if _has_drift_notification_today(decision_database, suggestion.symbol, now):
                    continue

                message = f"{suggestion.symbol} 目前權重 {suggestion.current_weight_pct:.1f}% 偏離目標 {suggestion.target_weight_pct:.1f}% 超過 {threshold_pct}%"
                result = record_notification(
                    decision_database,
                    "allocation_drift",
                    suggestion.symbol,
                    message,
                    now,
                    notify_os=notify_os,
                )
                if result is not None:
                    fired.append(message)

    return fired


def _has_reversal_notification_today(database: Path, symbol: str, now: datetime) -> bool:
    initialize(database)
    with database_connection(database) as connection:
        rows = connection.execute(
            "SELECT triggered_at FROM notification_log WHERE category = 'short_term_reversal' AND symbol = ?",
            (symbol,),
        ).fetchall()
    for row in rows:
        dt = datetime.fromisoformat(row[0])
        if dt.date() == now.date():
            return True
    return False


def check_short_term_reversal_triggers(
    decision_database: Path,
    history_database: Path,
    now: datetime,
    lookback: int = 5,
    drop_pct: float = 8.0,
    notify_os: bool = True,
) -> list[str]:
    """Evaluate watchlist items and owned holdings for short-term reversal triggers."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    # Local imports to avoid circular/unnecessary imports
    from transaction_ledger import calculate_holdings
    from watchlist_repository import list_items
    from short_term_reversal import calculate_short_term_reversal_for_symbol

    watchlist_symbols = {item.symbol for item in list_items(decision_database)}
    holding_symbols = {item.symbol for item in calculate_holdings(decision_database) if item.shares > 0}
    symbols = sorted(watchlist_symbols | holding_symbols)

    fired: list[str] = []

    for symbol in symbols:
        if _has_reversal_notification_today(decision_database, symbol, now):
            continue

        triggered = calculate_short_term_reversal_for_symbol(
            history_database, symbol, lookback=lookback, drop_pct=drop_pct
        )
        if triggered:
            message = f"{symbol} 近{lookback}日跌幅達{drop_pct:g}%以上，符合短期反彈觀察條件"
            result = record_notification(
                decision_database,
                "short_term_reversal",
                symbol,
                message,
                now,
                notify_os=notify_os,
            )
            if result is not None:
                fired.append(message)

    return fired
