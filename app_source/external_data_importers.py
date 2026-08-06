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
@dataclass(frozen=True, slots=True)
class InstitutionalFlow: trading_date: date; symbol: str; foreign_net_shares: int; trust_net_shares: int; dealer_net_shares: int; total_net_shares: int

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
        CREATE TABLE IF NOT EXISTS institutional_flow_history (
            trading_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            foreign_net_shares INTEGER NOT NULL,
            trust_net_shares INTEGER NOT NULL,
            dealer_net_shares INTEGER NOT NULL,
            total_net_shares INTEGER NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY(trading_date, symbol)
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


## global_risk_factor_score/global_risk_score_from_vix/latest_vix moved to
## sentiment_fear.py -- that version uses VIX historical percentile + 5-day
## change (matching score_fear()'s real methodology) instead of this file's
## simpler linear VIX-level mapping, and is now the single implementation
## factor_score_app.py / factor_score_store.py both call. Keeping two
## competing formulas for the same "global_risk" factor slot would let two
## code paths in the app silently disagree about the same number.

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


TAIFEX_DAILY_REPORT_URL = "https://openapi.taifex.com.tw/v1/DailyMarketReportFut"


def fetch_taifex_daily_report(timeout: int = 20) -> list[dict[str, object]]:
    """Fetch today's TAIFEX daily futures market report -- every contract,
    both trading sessions ("一般"=regular day session, "盤後"=after-hours,
    i.e. what's colloquially called 夜盤/night session).

    Live-verified 2026-08-06: this is a real, free, working OpenAPI endpoint
    with a TradingSession field distinguishing the two sessions (183 "盤後"
    rows present in a real sample fetch) -- night-session futures data does
    NOT actually require an official downloaded file, contrary to this
    project's own earlier notes. Like TWSE/TPEx's own daily snapshot
    endpoints already used elsewhere in this codebase, this only ever
    returns the current day -- no historical range query.

    Raises: urllib.error.URLError, json.JSONDecodeError, etc. on network/parsing failure.
    """
    request = urllib.request.Request(TAIFEX_DAILY_REPORT_URL, headers={"User-Agent": "StockAI-local-research/1.0"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, list) else []


def _optional_float(value: object) -> float | None:
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _optional_int(value: object) -> int | None:
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_taifex_daily_report(records: list[dict[str, object]]) -> list[TaifexDaily]:
    """Convert raw DailyMarketReportFut rows into TaifexDaily records.

    The real API varies by contract code AND contract month/week (e.g. TXF's
    near-month vs next-month vs weekly contracts all share the same
    "Contract" code) -- folding the month into the stored contract id (e.g.
    "TXF202608") keeps each distinct instrument from colliding under
    taifex_daily_reports' (trading_date, contract, session) primary key,
    which would otherwise silently overwrite all but one contract-month per
    session per day.

    Rows with an unparseable date, or missing contract/session, are skipped
    rather than raising -- matching this codebase's convention of tolerating
    a handful of malformed rows in a market-wide snapshot instead of failing
    the whole import."""
    parsed: list[TaifexDaily] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        date_str = str(row.get("Date", "")).strip()
        if len(date_str) != 8 or not date_str.isdigit():
            continue
        try:
            trading_date = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        except ValueError:
            continue
        contract = str(row.get("Contract", "")).strip()
        session = str(row.get("TradingSession", "")).strip()
        if not contract or not session:
            continue
        contract_month = str(row.get("ContractMonth(Week)", "")).strip()
        contract_id = f"{contract}{contract_month}" if contract_month else contract
        parsed.append(TaifexDaily(
            trading_date, contract_id, session,
            _optional_float(row.get("Open")),
            _optional_float(row.get("High")),
            _optional_float(row.get("Low")),
            _optional_float(row.get("Last")),
            _optional_int(row.get("Volume")),
            _optional_int(row.get("OpenInterest")),
        ))
    return parsed


TWSE_INSTITUTIONAL_FLOW_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"


def fetch_twse_institutional_flow_report(trading_date: date, timeout: int = 20) -> list[list[object]]:
    """Fetch TWSE's daily "三大法人買賣超" (three major institutional
    investors' net buy/sell) report for one date -- every listed security.

    Live-verified 2026-08-06: unlike DailyMarketReportFut, this endpoint
    genuinely supports historical per-date queries (confirmed: requesting
    two different real past dates returned different row counts and each
    response's own "date" field echoed back the requested date), so this
    could support backfill later; only "today" is wired in for now, matching
    the TAIFEX/market-index pattern.

    Returns the raw "data" rows (each a list of strings, comma-formatted
    numbers as text) exactly as TWSE returns them -- parse_twse_institutional_flow_report
    does the conversion.

    Raises: urllib.error.URLError, json.JSONDecodeError, etc. on network/parsing failure.
    """
    url = f"{TWSE_INSTITUTIONAL_FLOW_URL}?date={trading_date.strftime('%Y%m%d')}&selectType=ALL&response=json"
    request = urllib.request.Request(url, headers={"User-Agent": "StockAI-local-research/1.0"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    data = payload.get("data")
    return data if isinstance(data, list) else []


def parse_twse_institutional_flow_report(trading_date: date, records: list[list[object]]) -> list[InstitutionalFlow]:
    """Convert raw T86 rows into InstitutionalFlow records.

    Column layout confirmed live against a real response (index -> 中文欄位):
    0 證券代號, 4 外陸資買賣超股數(不含外資自營商), 10 投信買賣超股數,
    11 自營商買賣超股數(合計), 18 三大法人買賣超股數(合計).
    Rows with a non-4-digit symbol (ETFs/warrants/etc share this endpoint)
    or unparseable numbers are skipped rather than raising -- matching this
    codebase's convention of tolerating a handful of malformed rows in a
    market-wide snapshot instead of failing the whole import."""
    parsed: list[InstitutionalFlow] = []
    for row in records:
        if not isinstance(row, list) or len(row) < 19:
            continue
        symbol = str(row[0]).strip()
        if not symbol.isdigit() or len(symbol) != 4:
            continue
        foreign_net = _optional_int(row[4])
        trust_net = _optional_int(row[10])
        dealer_net = _optional_int(row[11])
        total_net = _optional_int(row[18])
        if None in (foreign_net, trust_net, dealer_net, total_net):
            continue
        parsed.append(InstitutionalFlow(trading_date, symbol, foreign_net, trust_net, dealer_net, total_net))
    return parsed


def import_institutional_flow(database: Path, records: list[InstitutionalFlow]) -> int:
    initialize(database)
    now = datetime.now().astimezone().isoformat()
    with database_connection(database) as c:
        c.executemany(
            "INSERT OR REPLACE INTO institutional_flow_history VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(x.trading_date.isoformat(), x.symbol, x.foreign_net_shares, x.trust_net_shares, x.dealer_net_shares, x.total_net_shares, now) for x in records],
        )
    return len(records)


TPEX_INSTITUTIONAL_FLOW_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"


def fetch_tpex_institutional_flow_report(timeout: int = 20) -> list[dict[str, object]]:
    """Fetch TPEx's (上櫃) daily three-major-institutional-investors report.

    Live-verified 2026-08-06: T86 (TWSE) only covers TWSE-listed symbols --
    confirmed real by its absence for 6182 (合晶, a TPEx stock) in a live
    T86 response -- this is the separate free, working TPEx endpoint that
    actually has it. Like TPEx's other openapi endpoints already used in
    this codebase (tpex_index, tpex_mainboard_daily_close_quotes), this only
    returns the latest available day -- no date query parameter.

    Raises: urllib.error.URLError, json.JSONDecodeError, etc. on network/parsing failure.
    """
    request = urllib.request.Request(TPEX_INSTITUTIONAL_FLOW_URL, headers={"User-Agent": "StockAI-local-research/1.0"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, list) else []


def parse_tpex_institutional_flow_report(records: list[dict[str, object]]) -> list[InstitutionalFlow]:
    """Convert raw tpex_3insti_daily_trading rows into InstitutionalFlow
    records. Field names confirmed live against a real response -- several
    have inconsistent spacing (TPEx's own JSON keys, not a typo here), so
    exact-match .get() calls are required rather than a cleaned-up guess."""
    parsed: list[InstitutionalFlow] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("SecuritiesCompanyCode", "")).strip()
        if not symbol.isdigit() or len(symbol) != 4:
            continue
        date_str = str(row.get("Date", "")).strip()
        try:
            trading_date = _parse_roc_date(date_str)
        except (ValueError, IndexError):
            continue
        foreign_net = _optional_int(row.get("ForeignInvestorsInclude MainlandAreaInvestors-Difference"))
        trust_net = _optional_int(row.get("SecuritiesInvestmentTrustCompanies-Difference"))
        dealer_net = _optional_int(row.get("Dealers-Difference"))
        total_net = _optional_int(row.get("TotalDifference"))
        if None in (foreign_net, trust_net, dealer_net, total_net):
            continue
        parsed.append(InstitutionalFlow(trading_date, symbol, foreign_net, trust_net, dealer_net, total_net))
    return parsed


def _parse_roc_date(date_str: str) -> date:
    """Parse a TPEx date string, which is ROC-format on some endpoints
    (tpex_3insti_daily_trading: "1150806" / "112/10/25") but plain Gregorian
    YYYYMMDD on others -- live-confirmed 2026-08-07: tpex_index now returns
    "20260803" rather than the ROC format it used when fetch_tpex_index was
    written, which silently zeroed out every TPEx index row forever (each
    row raised ValueError here and was swallowed by fetch_tpex_index's own
    per-row except-and-skip). An ROC year can never reach 4 digits, so an
    unambiguous length/magnitude check disambiguates the two formats."""
    date_str = date_str.strip()
    # Try splitting by "/" first
    if "/" in date_str:
        parts = date_str.split("/")
        if len(parts) == 3:
            year = int(parts[0])
            if len(parts[0]) == 4:  # already Gregorian, e.g. "2026/08/03"
                return date(year, int(parts[1]), int(parts[2]))
            return date(year + 1911, int(parts[1]), int(parts[2]))
    # Otherwise fall back to pure digits
    digits = "".join(c for c in date_str if c.isdigit())
    if len(digits) == 8:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
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

    # Must match by label, not position: live-confirmed 2026-08-07 that
    # MI_INDEX's first table lists "寶島股價指數" (a minor sub-index) at
    # data[0] and the actual TAIEX ("發行量加權股價指數", what "加權指數"
    # means everywhere else) at data[1] -- blindly taking data[0] silently
    # imported the wrong index into every downstream sentiment/market-context
    # calculation since this function was written.
    for table in tables:
        if not isinstance(table, dict) or "data" not in table:
            continue
        data = table["data"]
        if not isinstance(data, list):
            continue
        for row in data:
            if isinstance(row, list) and len(row) >= 2 and str(row[0]).strip() == "發行量加權股價指數":
                return float(str(row[1]).strip().replace(",", ""))
    return None


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
