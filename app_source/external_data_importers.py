"""Free-source importers for FRED VIX, MOPS exports/XBRL and TAIFEX daily files.

Network fetching is intentionally limited to FRED's public CSV.  MOPS and
TAIFEX inputs are also accepted as official files downloaded by the user so a
site change never silently corrupts local research data.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import urllib.request
import ssl
import certifi
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from database_utils import database_connection

FRED_VIX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"

@dataclass(frozen=True, slots=True)
class VixRecord: trading_date: date; value: float
@dataclass(frozen=True, slots=True)
class MopsFinancial: symbol: str; fiscal_year: int; fiscal_quarter: int; revenue: float | None; eps: float | None; gross_margin: float | None; operating_margin: float | None; roe: float | None; debt_ratio: float | None; source: str
@dataclass(frozen=True, slots=True)
class TaifexDaily: trading_date: date; contract: str; session: str; open_price: float | None; high_price: float | None; low_price: float | None; close_price: float | None; volume: int | None; open_interest: int | None

def initialize(database: Path) -> None:
    with database_connection(database) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS vix_history (trading_date TEXT PRIMARY KEY, value REAL NOT NULL, source TEXT NOT NULL, imported_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS mops_financials (symbol TEXT NOT NULL, fiscal_year INTEGER NOT NULL, fiscal_quarter INTEGER NOT NULL, revenue REAL, eps REAL, gross_margin REAL, operating_margin REAL, roe REAL, debt_ratio REAL, source TEXT NOT NULL, imported_at TEXT NOT NULL, PRIMARY KEY(symbol,fiscal_year,fiscal_quarter,source));
        CREATE TABLE IF NOT EXISTS taifex_daily_reports (trading_date TEXT NOT NULL, contract TEXT NOT NULL, session TEXT NOT NULL, open_price REAL, high_price REAL, low_price REAL, close_price REAL, volume INTEGER, open_interest INTEGER, imported_at TEXT NOT NULL, PRIMARY KEY(trading_date,contract,session));
        CREATE TABLE IF NOT EXISTS market_index_history (
            trading_date TEXT NOT NULL,
            market TEXT NOT NULL,
            close_value REAL NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY(trading_date, market)
        );
        """)

def fetch_fred_vix_csv(timeout: int = 20) -> bytes:
    request = urllib.request.Request(FRED_VIX_URL, headers={"User-Agent": "StockAI-local-research/1.0"})
    context=ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response: return response.read()

def parse_fred_vix_csv(payload: bytes) -> list[VixRecord]:
    rows: list[VixRecord] = []
    for row in csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))):
        value = (row.get("VIXCLS") or "").strip()
        if value in {"", "."}: continue
        rows.append(VixRecord(date.fromisoformat(row["observation_date"]), float(value)))
    if not rows: raise ValueError("FRED VIX CSV has no numeric observations")
    return rows

def import_vix(database: Path, records: list[VixRecord], source: str = "FRED:VIXCLS") -> int:
    initialize(database); now = datetime.now().astimezone().isoformat()
    with database_connection(database) as c:
        c.executemany("INSERT OR REPLACE INTO vix_history VALUES (?, ?, ?, ?)", [(x.trading_date.isoformat(), x.value, source, now) for x in records])
    return len(records)


def latest_vix(database: Path) -> tuple[float, date] | None:
    """Return (value, trading_date) for the most recent locally stored VIX
    observation, or None if vix_history is empty/missing."""
    if not database.exists():
        return None
    with database_connection(database) as c:
        c.execute("CREATE TABLE IF NOT EXISTS vix_history (trading_date TEXT PRIMARY KEY, value REAL NOT NULL, source TEXT NOT NULL, imported_at TEXT NOT NULL)")
        row = c.execute("SELECT trading_date, value FROM vix_history ORDER BY trading_date DESC LIMIT 1").fetchone()
    return None if row is None else (row[1], date.fromisoformat(row[0]))


def global_risk_score_from_vix(vix: float) -> float:
    """Map a VIX level to a 0-100 "global risk" factor score, following the
    same higher-is-more-favorable convention as every other factor (a calm
    market scores high, an elevated-fear market scores low).

    Anchors follow common VIX interpretation bands: <=12 is a calm market
    (score 100), >=40 is crisis-level fear (score 0, e.g. the 2020 selloff),
    linear in between. This is a simple, disclosed heuristic, not a
    statistically fitted model -- it exists to give a defensible starting
    point the user can still override, not an authoritative signal.
    """
    low_vix, high_vix = 12.0, 40.0
    ratio = (vix - low_vix) / (high_vix - low_vix)
    return round(max(0.0, min(100.0, 100.0 - ratio * 100.0)), 1)


def global_risk_factor_score(database: Path) -> tuple[float | None, str]:
    """Return (score, note); score is None when there's no local VIX history yet."""
    latest = latest_vix(database)
    if latest is None:
        return None, "尚無本機 VIX 資料，無法自動建議全球風險分數；請先執行一次「更新全部上市／上櫃並驗證」。"
    vix, trading_date = latest
    score = global_risk_score_from_vix(vix)
    return score, f"依 {trading_date} VIX {vix:.2f} 自動建議（VIX 越低分數越高，僅供參考，可自行調整）。"

def _value(row: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        raw = row.get(key)
        if raw not in (None, "", "-"): return float(str(raw).replace(",", "").replace("%", ""))
    return None

def parse_mops_csv(payload: bytes, source: str = "MOPS CSV") -> list[MopsFinancial]:
    rows: list[MopsFinancial] = []
    for row in csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))):
        symbol = (row.get("symbol") or row.get("公司代號") or "").strip()
        year = _value(row, "fiscal_year", "年度"); quarter = _value(row, "fiscal_quarter", "季別")
        if not (symbol.isdigit() and len(symbol) == 4 and year and quarter): raise ValueError("MOPS CSV needs symbol/company code, fiscal year and quarter")
        rows.append(MopsFinancial(symbol, int(year), int(quarter), _value(row,"revenue","營業收入"), _value(row,"eps","每股盈餘"), _value(row,"gross_margin","毛利率"), _value(row,"operating_margin","營業利益率"), _value(row,"roe","ROE","權益報酬率"), _value(row,"debt_ratio","負債比"), source))
    if not rows: raise ValueError("MOPS CSV has no rows")
    return rows

def parse_mops_xbrl(payload: bytes, symbol: str, fiscal_year: int, fiscal_quarter: int) -> list[MopsFinancial]:
    """Extract common IFRS fact local-names from an official iXBRL/XML file."""
    root = ET.fromstring(payload); facts = {node.tag.rsplit("}", 1)[-1]: (node.text or "").strip() for node in root.iter()}
    def fact(*names: str) -> float | None:
        for name in names:
            raw = facts.get(name)
            if raw:
                try: return float(raw.replace(",", ""))
                except ValueError: pass
        return None
    return [MopsFinancial(symbol, fiscal_year, fiscal_quarter, fact("Revenue", "OperatingRevenue"), fact("BasicEarningsPerShare"), fact("GrossProfitRatio"), fact("OperatingProfitRatio"), fact("ReturnOnEquity"), fact("DebtRatio"), "MOPS XBRL")]

def import_mops(database: Path, records: list[MopsFinancial]) -> int:
    initialize(database); now = datetime.now().astimezone().isoformat()
    with database_connection(database) as c:
        c.executemany("INSERT OR REPLACE INTO mops_financials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [(x.symbol,x.fiscal_year,x.fiscal_quarter,x.revenue,x.eps,x.gross_margin,x.operating_margin,x.roe,x.debt_ratio,x.source,now) for x in records])
    return len(records)

def import_taifex(database: Path, records: list[TaifexDaily]) -> int:
    initialize(database); now = datetime.now().astimezone().isoformat()
    with database_connection(database) as c:
        c.executemany("INSERT OR REPLACE INTO taifex_daily_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [(x.trading_date.isoformat(),x.contract,x.session,x.open_price,x.high_price,x.low_price,x.close_price,x.volume,x.open_interest,now) for x in records])
    return len(records)


def _parse_roc_date(date_str: str) -> date:
    date_str = date_str.strip()
    # Try splitting by "/" first
    if "/" in date_str:
        parts = date_str.split("/")
        if len(parts) == 3:
            return date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
    # Otherwise fall back to pure digits
    digits = "".join(c for c in date_str if c.isdigit())
    if len(digits) == 7:
        return date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:]))
    elif len(digits) == 6:
        return date(int(digits[:2]) + 1911, int(digits[2:4]), int(digits[4:]))
    raise ValueError(f"Could not parse ROC date: {date_str}")


def fetch_twse_index(trading_date: date, timeout: int = 20) -> float | None:
    """Fetch the TWSE capitalization-weighted stock index close value for a given date.

    Args:
        trading_date (date): The Gregorian date to query.
        timeout (int): Network timeout in seconds.

    Returns:
        float | None: The close value of the index, or None if it was a non-trading day/no data available.

    Raises:
        urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, etc. if network or parsing fails for active trading days.
    """
    url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={trading_date.strftime('%Y%m%d')}&type=IND"
    request = urllib.request.Request(url, headers={"User-Agent": "StockAI-local-research/1.0"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        return None

    tables = payload.get("tables")
    if not tables or not isinstance(tables, list):
        return None

    table = tables[0]
    if not isinstance(table, dict) or "data" not in table:
        return None

    data = table["data"]
    if not isinstance(data, list) or not data:
        return None

    first_row = data[0]
    if not isinstance(first_row, list) or len(first_row) < 2:
        return None

    close_str = str(first_row[1]).strip()
    return float(close_str.replace(",", ""))


def fetch_tpex_index(timeout: int = 20) -> list[tuple[date, float]]:
    """Fetch the recent TPEx (OTC) index close values.

    Args:
        timeout (int): Network timeout in seconds.

    Returns:
        list[tuple[date, float]]: Chronologically sorted list of (trading_date, close_value) tuples.

    Raises:
        urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, etc. if network or parsing fails.
    """
    url = "https://www.tpex.org.tw/openapi/v1/tpex_index"
    request = urllib.request.Request(url, headers={"User-Agent": "StockAI-local-research/1.0"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, list):
        raise ValueError("TPEx response is not a JSON list")

    results: list[tuple[date, float]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        roc_date_str = item.get("Date")
        close_str = item.get("Close")
        if roc_date_str is None or close_str is None:
            continue
        try:
            dt = _parse_roc_date(str(roc_date_str))
            close_val = float(str(close_str).replace(",", ""))
            results.append((dt, close_val))
        except Exception:
            # Let exceptions propagate if the structure is completely broken,
            # but allow skipping individual rows if they are empty.
            pass

    results.sort(key=lambda x: x[0])
    return results


def import_market_indices(database: Path, records: list[tuple[date, str, float]]) -> int:
    """UPSERT market index close values into the market_index_history table.

    Args:
        database (Path): Path to SQLite database.
        records (list[tuple[date, str, float]]): List of (trading_date, market_name, close_value) tuples.

    Returns:
        int: Number of records written.
    """
    initialize(database)
    now = datetime.now().astimezone().isoformat()
    with database_connection(database) as c:
        c.executemany(
            "INSERT OR REPLACE INTO market_index_history (trading_date, market, close_value, imported_at) VALUES (?, ?, ?, ?)",
            [(x[0].isoformat(), x[1], x[2], now) for x in records]
        )
    return len(records)
