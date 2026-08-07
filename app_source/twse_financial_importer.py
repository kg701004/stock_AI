"""Import official TWSE OpenAPI financial JSON, downloaded automatically or manually."""
from __future__ import annotations
import json
import ssl
import urllib.request
from pathlib import Path
import certifi
from external_data_importers import MopsFinancial

def parse_profitability_json(payload: bytes, source: str = "TWSE OpenAPI t187ap17_L") -> list[MopsFinancial]:
    rows = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(rows, list): raise ValueError("TWSE payload must be a JSON array")
    result = []
    for row in rows:
        symbol = str(row.get("公司代號", "")).strip()
        try:
            year, quarter = int(str(row["年度"])), int(str(row["季別"]))
        except (KeyError, ValueError) as error: raise ValueError("TWSE row needs company code, fiscal year and quarter") from error
        if not (symbol.isdigit() and len(symbol) == 4 and 1 <= quarter <= 4): continue
        def number(*names: str) -> float | None:
            for name in names:
                raw = str(row.get(name, "")).replace(",", "").replace("%", "").strip()
                if raw and raw not in {"-", "--"}:
                    try: return float(raw)
                    except ValueError: raise ValueError(f"invalid {name} for {symbol}")
            return None
        result.append(MopsFinancial(symbol, year, quarter, number("營業收入(百萬元)", "營業收入"), None, number("毛利率(%)(營業毛利)/(營業收入)", "毛利率(%)"), number("營業利益率(%)(營業利益)/(營業收入)", "營業利益率(%)"), None, None, source))
    if not result: raise ValueError("TWSE JSON has no valid financial records")
    return result

def load_profitability_json(path: Path) -> list[MopsFinancial]:
    return parse_profitability_json(path.read_bytes(), f"TWSE manual JSON:{path.name}")


GENERAL_INDUSTRY_INCOME_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"
GENERAL_INDUSTRY_BALANCE_SHEET_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci"


def _fetch_json(url: str, timeout_seconds: int) -> list[dict[str, object]]:
    request = urllib.request.Request(url, headers={"User-Agent": "StockAI-local-research/1.0"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout_seconds, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("TWSE financial statement response must be a JSON list")
    return payload


def fetch_general_industry_income_statement(timeout_seconds: int = 20) -> list[dict[str, object]]:
    """Fetch TWSE's 上市公司綜合損益表(一般業) -- t187ap06_L_ci.

    Live-verified 2026-08-07: real, free, working. Covers only 一般業
    (general industry, i.e. not financial holding/banking/securities/
    insurance, each of which has its own separate endpoint with different
    field names -- e.g. t187ap06_L_fh/_bd/_ins/_basi -- not covered by this
    importer). Also only includes whichever companies have ALREADY filed
    their quarterly report as of the request time -- confirmed live: right
    after Q2 2026 ended, only 176 of the ~1,000+ 一般業 companies had filed
    (TSMC not among them), growing as the ~45-day filing window progresses.
    This is a real reporting-season timing characteristic of the data
    itself, not a bug or a missing query parameter (the endpoint takes none).
    """
    return _fetch_json(GENERAL_INDUSTRY_INCOME_URL, timeout_seconds)


def fetch_general_industry_balance_sheet(timeout_seconds: int = 20) -> list[dict[str, object]]:
    """Fetch TWSE's 上市公司資產負債表(一般業) -- t187ap07_L_ci. Same scope
    and filing-timing characteristics as fetch_general_industry_income_statement."""
    return _fetch_json(GENERAL_INDUSTRY_BALANCE_SHEET_URL, timeout_seconds)


def _number(value: object) -> float | None:
    text = str(value).replace(",", "").strip()
    if text in {"", "-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_general_industry_financials(
    income_records: list[dict[str, object]], balance_records: list[dict[str, object]],
) -> list[MopsFinancial]:
    """Merge income-statement and balance-sheet rows (joined on symbol/year/
    quarter) into MopsFinancial records for 一般業 (general industry) only.

    gross_margin/operating_margin are computed here (revenue-relative
    percentages) rather than taken from TWSE's separate t187ap17_L summary
    table, but cross-checked to match it exactly for a real company (1215:
    18.27% gross margin, 9.26% operating margin, both confirmed live).
    roe uses this single quarter's net income over equity (NOT annualized --
    labelled accordingly in the stored source string) since only one
    quarter's income statement is available per call; debt_ratio is a
    point-in-time balance-sheet ratio, needs no such caveat."""
    source = "TWSE t187ap06/07_L_ci (一般業，單季未年化ROE)"
    balance_by_key = {}
    for row in balance_records:
        symbol = str(row.get("公司代號", "")).strip()
        if not (symbol.isdigit() and len(symbol) == 4):
            continue
        try:
            key = (symbol, int(str(row["年度"])), int(str(row["季別"])))
        except (KeyError, ValueError):
            continue
        balance_by_key[key] = row

    parsed: list[MopsFinancial] = []
    for row in income_records:
        symbol = str(row.get("公司代號", "")).strip()
        if not (symbol.isdigit() and len(symbol) == 4):
            continue
        try:
            year, quarter = int(str(row["年度"])), int(str(row["季別"]))
        except (KeyError, ValueError):
            continue
        if not 1 <= quarter <= 4:
            continue
        revenue = _number(row.get("營業收入"))
        gross_profit = _number(row.get("營業毛利（毛損）淨額"))
        operating_income = _number(row.get("營業利益（損失）"))
        net_income_to_parent = _number(row.get("淨利（淨損）歸屬於母公司業主"))
        gross_margin = gross_profit / revenue * 100 if revenue and gross_profit is not None else None
        operating_margin = operating_income / revenue * 100 if revenue and operating_income is not None else None

        balance = balance_by_key.get((symbol, year, quarter))
        roe, debt_ratio = None, None
        if balance is not None:
            total_assets = _number(balance.get("資產總計"))
            total_liabilities = _number(balance.get("負債總計"))
            parent_equity = _number(balance.get("歸屬於母公司業主之權益合計"))
            if total_assets:
                debt_ratio = total_liabilities / total_assets * 100 if total_liabilities is not None else None
            if parent_equity and net_income_to_parent is not None:
                roe = net_income_to_parent / parent_equity * 100

        parsed.append(MopsFinancial(symbol, year, quarter, revenue, None, gross_margin, operating_margin, roe, debt_ratio, source))
    return parsed


def update_general_industry_financials(database: Path, timeout_seconds: int = 20) -> int:
    """Fetch + merge + store in one call; the daily-update entry point uses this."""
    from external_data_importers import import_mops
    income = fetch_general_industry_income_statement(timeout_seconds)
    balance = fetch_general_industry_balance_sheet(timeout_seconds)
    return import_mops(database, parse_general_industry_financials(income, balance))
