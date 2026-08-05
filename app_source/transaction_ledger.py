"""SQLite transaction ledger supporting multi-owner, multi-lot stock accounting."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from database_utils import database_connection


@dataclass(frozen=True, slots=True)
class Transaction:
    id: int | None
    owner: str
    symbol: str
    traded_at: datetime
    side: str
    shares: int
    price: float
    fee: float = 0.0
    note: str = ""

    def __post_init__(self) -> None:
        if not self.owner.strip() or not self.symbol.isdigit() or len(self.symbol) != 4:
            raise ValueError("owner and four-digit symbol are required")
        if self.side not in {"BUY", "SELL"} or self.shares <= 0 or self.price <= 0 or self.fee < 0:
            raise ValueError("invalid transaction side, quantity, price, or fee")
        if self.traded_at.tzinfo is None:
            raise ValueError("traded_at must include a timezone")


@dataclass(frozen=True, slots=True)
class LedgerHolding:
    owner: str
    symbol: str
    shares: int
    average_cost: float
    current_price: float | None
    cost_value: float
    market_value: float | None
    unrealized_profit: float | None
    unrealized_profit_pct: float | None
    realized_profit: float
    total_fees: float


def initialize(database: Path) -> None:
    """Create persistent transaction and last-price tables."""
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY, owner TEXT NOT NULL, symbol TEXT NOT NULL,
                traded_at TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                shares INTEGER NOT NULL CHECK(shares > 0), price REAL NOT NULL CHECK(price > 0),
                fee REAL NOT NULL DEFAULT 0 CHECK(fee >= 0), note TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_transactions_owner_symbol_time
            ON transactions(owner, symbol, traded_at, id);
            CREATE TABLE IF NOT EXISTS current_prices (
                symbol TEXT PRIMARY KEY, price REAL NOT NULL CHECK(price > 0), as_of TEXT NOT NULL
            );
        """)


def add_transaction(database: Path, transaction: Transaction) -> int:
    """Append a transaction and validate inventory in chronological trade order."""
    initialize(database)
    with database_connection(database) as connection:
        cursor = connection.execute(
            "INSERT INTO transactions(owner, symbol, traded_at, side, shares, price, fee, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (transaction.owner, transaction.symbol, transaction.traded_at.isoformat(), transaction.side, transaction.shares, transaction.price, transaction.fee, transaction.note),
        )
        _validate_transaction_rows(connection.execute("SELECT id, owner, symbol, traded_at, side, shares, price, fee, note FROM transactions ORDER BY owner, symbol, traded_at, id").fetchall())
    return int(cursor.lastrowid)


def delete_transaction(database: Path, transaction_id: int) -> None:
    """Delete a transaction only when remaining chronological history stays valid."""
    with database_connection(database) as connection:
        connection.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        _validate_transaction_rows(connection.execute("SELECT id, owner, symbol, traded_at, side, shares, price, fee, note FROM transactions ORDER BY owner, symbol, traded_at, id").fetchall())


def set_current_price(database: Path, symbol: str, price: float, as_of: datetime) -> None:
    """Store a latest known price used only for valuation, not as a transaction."""
    if not symbol.isdigit() or len(symbol) != 4 or price <= 0 or as_of.tzinfo is None:
        raise ValueError("invalid current-price input")
    initialize(database)
    with database_connection(database) as connection:
        connection.execute("INSERT OR REPLACE INTO current_prices VALUES (?, ?, ?)", (symbol, price, as_of.isoformat()))


def list_transactions(database: Path, owner: str | None = None, symbol: str | None = None) -> list[Transaction]:
    """Return chronological transaction history, optionally filtered by holding."""
    initialize(database)
    query, values = "SELECT id, owner, symbol, traded_at, side, shares, price, fee, note FROM transactions", []
    clauses = []
    if owner is not None:
        clauses.append("owner = ?"); values.append(owner)
    if symbol is not None:
        clauses.append("symbol = ?"); values.append(symbol)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY owner, symbol, traded_at, id"
    with database_connection(database) as connection:
        rows = connection.execute(query, values).fetchall()
    return [Transaction(row[0], row[1], row[2], datetime.fromisoformat(row[3]), row[4], row[5], row[6], row[7], row[8]) for row in rows]


def _validate_transaction_rows(rows: list[tuple[object, ...]]) -> None:
    """Reject a ledger whose time-ordered sales exceed shares held at that time."""
    inventory: dict[tuple[str, str], int] = {}
    for row in rows:
        _, owner, symbol, traded_at, side, shares, *_ = row
        key = (str(owner), str(symbol))
        inventory.setdefault(key, 0)
        if side == "BUY":
            inventory[key] += int(shares)
        elif int(shares) > inventory[key]:
            raise ValueError(f"transaction at {traded_at} sells {shares} shares of {symbol} before sufficient shares were held")
        else:
            inventory[key] -= int(shares)


def calculate_holdings(database: Path, include_closed: bool = False) -> list[LedgerHolding]:
    """Calculate weighted-average cost, realized P/L, and unrealized P/L from all lots."""
    transactions = list_transactions(database)
    _validate_transaction_rows([(item.id, item.owner, item.symbol, item.traded_at.isoformat(), item.side, item.shares, item.price, item.fee, item.note) for item in transactions])
    prices: dict[str, float] = {}
    with database_connection(database) as connection:
        for symbol, price in connection.execute("SELECT symbol, price FROM current_prices"):
            prices[symbol] = price
    states: dict[tuple[str, str], dict[str, float]] = {}
    for item in transactions:
        state = states.setdefault((item.owner, item.symbol), {"shares": 0, "cost": 0.0, "realized": 0.0, "fees": 0.0})
        if item.side == "BUY":
            state["shares"] += item.shares
            state["cost"] += item.shares * item.price + item.fee
        else:
            average = state["cost"] / state["shares"]
            state["realized"] += item.shares * item.price - item.fee - item.shares * average
            state["cost"] -= item.shares * average
            state["shares"] -= item.shares
        state["fees"] += item.fee
    result: list[LedgerHolding] = []
    for (owner, symbol), state in states.items():
        shares = int(state["shares"])
        if shares == 0 and not include_closed:
            continue
        average_cost = 0 if shares == 0 else state["cost"] / shares
        price = prices.get(symbol)
        market_value = 0 if shares == 0 else (None if price is None else shares * price)
        unrealized = 0 if shares == 0 else (None if market_value is None else market_value - state["cost"])
        unrealized_pct = 0 if shares == 0 else (None if unrealized is None else unrealized / state["cost"] * 100)
        result.append(LedgerHolding(owner, symbol, shares, round(average_cost, 4), price, round(state["cost"], 2), None if market_value is None else round(market_value, 2), None if unrealized is None else round(unrealized, 2), None if unrealized_pct is None else round(unrealized_pct, 2), round(state["realized"], 2), round(state["fees"], 2)))
    return sorted(result, key=lambda item: (item.owner, item.symbol))


def owner_summary(database: Path, owner: str) -> dict[str, float]:
    """Return owner-level realized/unrealized totals for the selected ledger owner."""
    holdings = [item for item in calculate_holdings(database, include_closed=True) if item.owner == owner]
    return {
        "market_value": round(sum(item.market_value or 0 for item in holdings), 2),
        "cost_value": round(sum(item.cost_value for item in holdings), 2),
        "unrealized_profit": round(sum(item.unrealized_profit or 0 for item in holdings), 2),
        "realized_profit": round(sum(item.realized_profit for item in holdings), 2),
        "fees": round(sum(item.total_fees for item in holdings), 2),
    }
