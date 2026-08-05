"""Ex-dividend/ex-rights back-adjustment for historical daily bars.

Without this, a stock's raw close price shows an artificial gap down on its
ex-dividend date (the reference price is mechanically reduced by the payout),
which corrupts moving averages and support/resistance levels computed from
technical_layers.py -- exactly around distribution events, which are common
for Taiwan's high-dividend-yield stocks and ETFs (0056, 00878, etc.).

Standard backward-adjustment method: every bar dated strictly before an
ex-date is multiplied by that event's adjustment factor (reference_price /
pre_close); multiple events compound multiplicatively going further back.
"""
from __future__ import annotations

import json
import ssl
from datetime import date
from typing import Iterable
from urllib.request import Request, urlopen
import certifi

from database_utils import database_connection
from historical_storage import DailyBar
from pathlib import Path

TWSE_EX_RIGHTS_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"


def ensure_schema(connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS ex_rights_events (
            symbol TEXT NOT NULL, ex_date TEXT NOT NULL,
            pre_close REAL NOT NULL, reference_price REAL NOT NULL, adjustment_factor REAL NOT NULL,
            PRIMARY KEY (symbol, ex_date)
        )
    """)


def fetch_ex_rights_events(start: date, end: date, timeout_seconds: int = 20) -> list[dict[str, object]]:
    """Fetch market-wide ex-dividend/ex-rights calculation results for [start, end].

    Verified against the real endpoint: the single `date` parameter this
    endpoint also accepts does NOT scope to that month -- it silently ignores
    it and always returns the same near-term events regardless of what is
    passed. `startDate`/`endDate` is the parameter pair that actually performs
    a historical range query (confirmed by real data spanning the requested
    year, not just "today"). `stockNo` does not filter server-side either;
    results always cover the whole market and must be filtered client-side.
    """
    url = f"{TWSE_EX_RIGHTS_URL}?startDate={start.strftime('%Y%m%d')}&endDate={end.strftime('%Y%m%d')}&response=json"
    request = Request(url, headers={"User-Agent": "StockAI-OfflineResearch/1.0 contact: local-user"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout_seconds, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("stat") != "OK":
        return []
    return payload.get("data", [])


def parse_ex_rights_events(records: Iterable[list[object]]) -> list[tuple[str, date, float, float]]:
    """Rows are [ROC date, symbol, name, pre_close, reference_price, ...]. Returns (symbol, ex_date, pre_close, reference_price)."""
    events: list[tuple[str, date, float, float]] = []
    for row in records:
        if not isinstance(row, list) or len(row) < 5:
            continue
        try:
            roc_year, roc_month, roc_day = (int(part) for part in str(row[0]).replace("年", "/").replace("月", "/").replace("日", "").split("/"))
            ex_date = date(roc_year + 1911, roc_month, roc_day)
            symbol = str(row[1]).strip()
            pre_close = float(str(row[3]).replace(",", ""))
            reference_price = float(str(row[4]).replace(",", ""))
            if not (symbol.isdigit() and len(symbol) == 4) or pre_close <= 0:
                continue
            events.append((symbol, ex_date, pre_close, reference_price))
        except (ValueError, IndexError, ZeroDivisionError):
            continue
    return events


def store_events(database: Path, events: Iterable[tuple[str, date, float, float]]) -> int:
    rows = list(events)
    if not rows:
        return 0
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        ensure_schema(connection)
        connection.executemany(
            "INSERT OR REPLACE INTO ex_rights_events VALUES (?, ?, ?, ?, ?)",
            [(symbol, ex_date.isoformat(), pre_close, reference_price, reference_price / pre_close) for symbol, ex_date, pre_close, reference_price in rows],
        )
    return len(rows)


def has_any_ex_rights_data(database: Path) -> bool:
    """Whether the market-wide ex_rights_events table has ever been
    populated at all -- distinguishes "we checked and there's genuinely
    nothing upcoming" from "we have never fetched this, so we simply don't
    know" (a fresh install, or one where the daily update has never run)."""
    if not database.exists():
        return False
    with database_connection(database) as connection:
        ensure_schema(connection)
        return connection.execute("SELECT 1 FROM ex_rights_events LIMIT 1").fetchone() is not None


def next_ex_rights_event(database: Path, symbol: str, as_of: date) -> date | None:
    """Earliest known ex_date on or after `as_of` for this symbol, or None if
    none is currently known within the locally-fetched window."""
    if not database.exists():
        return None
    with database_connection(database) as connection:
        ensure_schema(connection)
        row = connection.execute(
            "SELECT ex_date FROM ex_rights_events WHERE symbol = ? AND ex_date >= ? ORDER BY ex_date LIMIT 1",
            (symbol, as_of.isoformat()),
        ).fetchone()
    return None if row is None else date.fromisoformat(row[0])


def events_score_from_days_ahead(days_ahead: int | None) -> float:
    """No known upcoming ex-dividend/rights event -> 70 (calmer, favorable).
    An event within the next 7 days -> 40 (near-term price-reference
    adjustment to be aware of); 8-30 days out -> 55; linear does not apply
    here since this is inherently a coarse "how soon" bucket, not a
    continuous quantity like VIX or a valuation ratio."""
    if days_ahead is None:
        return 70.0
    if days_ahead <= 7:
        return 40.0
    if days_ahead <= 30:
        return 55.0
    return 70.0


def events_factor_score(database: Path, symbol: str, as_of: date | None = None) -> tuple[float | None, str]:
    """Return (score, note). "No known upcoming event" is only ever treated
    as a real, meaningful answer -- not a fabricated "all clear" -- when the
    market-wide ex_rights_events table has actually been populated by at
    least one real daily update (see run_all_public_daily_updates's rolling
    30-day fetch); on a fresh install where it's never run, this honestly
    returns None instead of a confident 70."""
    as_of = as_of or date.today()
    if not has_any_ex_rights_data(database):
        return None, "本機尚無除權息事件資料（需先執行過一次「更新全部上市／上櫃並驗證」），無法自動建議事件風險分數。"
    next_event = next_ex_rights_event(database, symbol, as_of)
    if next_event is None:
        return events_score_from_days_ahead(None), "本機資料中查無近期除權息事件，暫視為無立即事件風險（僅供參考，可自行調整）。"
    days_ahead = (next_event - as_of).days
    return events_score_from_days_ahead(days_ahead), f"下一次除權息日 {next_event}（{days_ahead} 天後）自動建議（僅供參考，可自行調整）。"


def load_adjustment_factors(database: Path, symbol: str) -> list[tuple[date, float]]:
    """Return [(ex_date, factor), ...] for one symbol, oldest first."""
    if not database.exists():
        return []
    with database_connection(database) as connection:
        ensure_schema(connection)
        rows = connection.execute(
            "SELECT ex_date, adjustment_factor FROM ex_rights_events WHERE symbol = ? ORDER BY ex_date",
            (symbol,),
        ).fetchall()
    return [(date.fromisoformat(ex_date), factor) for ex_date, factor in rows]


def adjust_bars(bars: list[DailyBar], events: list[tuple[date, float]]) -> list[DailyBar]:
    """Back-adjust OHLC (not volume) for every ex-dividend/rights event after each bar's date."""
    if not events:
        return bars
    adjusted: list[DailyBar] = []
    for bar in bars:
        multiplier = 1.0
        for ex_date, factor in events:
            if ex_date > bar.trading_date:
                multiplier *= factor
        if multiplier == 1.0:
            adjusted.append(bar)
        else:
            adjusted.append(DailyBar(
                symbol=bar.symbol, trading_date=bar.trading_date,
                open_price=round(bar.open_price * multiplier, 4), high_price=round(bar.high_price * multiplier, 4),
                low_price=round(bar.low_price * multiplier, 4), close_price=round(bar.close_price * multiplier, 4),
                volume=bar.volume, source=bar.source, published_at=bar.published_at,
            ))
    return adjusted
