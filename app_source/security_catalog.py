"""Securities master lookup, auto-populated from daily TWSE/TPEx snapshots.

Replaces the old 3-entry hardcoded catalog: the `securities` table grows on
its own every time a daily update runs, and sector classification is
refreshed from TWSE's public company-basic-info dataset.
"""
from __future__ import annotations

import json
import ssl
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen
import certifi
import sqlite3

from database_utils import database_connection

TWSE_COMPANY_BASIC_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"

# TWSE's standard listed-company industry classification codes (from the
# t187ap03_L open dataset above); stable and rarely revised.
INDUSTRY_NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械",
    "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業",
    "12": "汽車工業", "13": "電子工業", "14": "建材營造業", "15": "航運業", "16": "觀光事業",
    "17": "金融保險業", "18": "貿易百貨業", "19": "綜合", "20": "其他業", "21": "化學工業",
    "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
    "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業", "31": "其他電子業",
    "32": "文化創意業", "33": "農業科技業", "34": "電子商務業", "80": "管理股票",
}


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS securities (
            symbol TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT NOT NULL,
            sector TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
        )
    """)


def upsert_from_daily_snapshot(database: Path, entries: Iterable[tuple[str, str]], market: str, seen_at: str) -> int:
    """Grow/refresh the catalog from (symbol, name) pairs pulled out of a daily snapshot."""
    rows = list(entries)
    if not rows:
        return 0
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        ensure_schema(connection)
        connection.executemany(
            """
            INSERT INTO securities(symbol, name, market, sector, first_seen, last_seen) VALUES (?, ?, ?, NULL, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, market=excluded.market, last_seen=excluded.last_seen
            """,
            [(symbol, name, market, seen_at, seen_at) for symbol, name in rows],
        )
    return len(rows)


def upsert_sectors(database: Path, entries: Iterable[tuple[str, str]]) -> int:
    """Refresh sector classification from (symbol, industry_code) pairs; only touches known symbols."""
    rows = list(entries)
    if not rows:
        return 0
    with database_connection(database) as connection:
        ensure_schema(connection)
        connection.executemany(
            "UPDATE securities SET sector = ? WHERE symbol = ?",
            [(INDUSTRY_NAMES.get(code, code), symbol) for symbol, code in rows],
        )
    return len(rows)


def fetch_company_basic_info(timeout_seconds: int = 20) -> list[dict[str, object]]:
    """Fetch TWSE's public listed-company basic-info dataset (code/name/industry)."""
    request = Request(TWSE_COMPANY_BASIC_INFO_URL, headers={"User-Agent": "StockAI-OfflineResearch/1.0 contact: local-user"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout_seconds, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("TWSE company basic info response must be a JSON list")
    return payload


def parse_company_basic_info(records: Iterable[dict[str, object]]) -> list[tuple[str, str]]:
    """Extract (symbol, industry_code) pairs from the raw company-basic-info payload."""
    entries: list[tuple[str, str]] = []
    for record in records:
        symbol = str(record.get("公司代號", "")).strip()
        code = str(record.get("產業別", "")).strip()
        if symbol.isdigit() and len(symbol) == 4 and code:
            entries.append((symbol, code))
    return entries


def resolve(database: Path, query: str) -> str:
    """Resolve a four-digit code, exact name, or unambiguous partial name to a symbol."""
    value = query.strip().casefold()
    with database_connection(database) as connection:
        ensure_schema(connection)
        rows = connection.execute("SELECT symbol, name FROM securities").fetchall()
    if not rows:
        raise ValueError("本機股票名錄尚未建立；請先在「資料管理」執行一次更新全部上市／上櫃。")
    for symbol, _name in rows:
        if value == symbol.casefold():
            return symbol
    matches = [symbol for symbol, name in rows if value in symbol.casefold() or value in name.casefold()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError("找不到代號或名稱；請輸入四碼代號，或先更新本機股票名錄。")
    raise ValueError("名稱對應到多檔股票，請改輸入四碼代號。")


def lookup_market(database: Path, symbol: str) -> str | None:
    """Return 'TWSE'/'TPEx' if the symbol is catalogued, else None (never seen in a daily snapshot yet)."""
    with database_connection(database) as connection:
        ensure_schema(connection)
        row = connection.execute("SELECT market FROM securities WHERE symbol = ?", (symbol,)).fetchone()
    return None if row is None else row[0]


def lookup_name(database: Path, symbol: str) -> str | None:
    """Return the catalogued name for a symbol, or None (never seen in a daily snapshot yet)."""
    with database_connection(database) as connection:
        ensure_schema(connection)
        row = connection.execute("SELECT name FROM securities WHERE symbol = ?", (symbol,)).fetchone()
    return None if row is None else row[0]


def list_all_symbols(database: Path) -> list[str]:
    """Every symbol ever seen in a daily snapshot (TWSE + TPEx combined),
    sorted. Empty if the catalog hasn't been built yet (no daily update run
    yet) -- callers should treat that the same as "nothing to do", not an error."""
    if not database.exists():
        return []
    with database_connection(database) as connection:
        ensure_schema(connection)
        rows = connection.execute("SELECT symbol FROM securities ORDER BY symbol").fetchall()
    return [row[0] for row in rows]


def is_etf(symbol: str) -> bool:
    """TWSE reserves the 00-prefixed code range for ETFs/ETNs."""
    return symbol.startswith("00")


def load_security_metadata(database: Path, symbols: Iterable[str] | None = None) -> dict[str, "SecurityMetadata"]:
    """Sector/beta lookup for every catalogued symbol, replacing the 2-stock sample CSV.

    Beta defaults to 1.0 (market-average) for every symbol UNLESS `symbols`
    is given, in which case those specific symbols get a real Beta computed
    from local daily-bar history vs a benchmark (portfolio_advanced_risk.
    compute_symbol_beta) -- still falling back to 1.0 when there isn't
    enough locally archived history yet, the same honest "unknown == average"
    convention as before, never a fabricated number either way.

    `symbols` is opt-in (not applied to the whole catalog by default) because
    computing a real Beta issues a couple of extra queries per symbol; the
    securities catalog can hold 1000+ symbols from daily snapshots while a
    caller typically only ever needs Beta for a handful of actually-held or
    actually-tracked stocks.
    """
    from portfolio_advanced_risk import compute_symbol_beta
    from portfolio_risk import SecurityMetadata
    with database_connection(database) as connection:
        ensure_schema(connection)
        rows = connection.execute("SELECT symbol, sector FROM securities").fetchall()
    wanted = None if symbols is None else set(symbols)
    result = {}
    for symbol, sector in rows:
        beta = 1.0
        if wanted is not None and symbol in wanted:
            computed = compute_symbol_beta(database, symbol)
            if computed is not None:
                beta = computed
        result[symbol] = SecurityMetadata(symbol, sector or "未分類", beta)
    return result
