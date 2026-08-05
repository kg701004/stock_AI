"""TPEx public end-of-day daily-quote adapter using the shared DailyBar model."""

from __future__ import annotations

import json
import ssl
from datetime import date, datetime
from typing import Iterable
from urllib.request import Request, urlopen
import certifi

from historical_storage import DailyBar
from twse_daily_importer import _number


TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"


def fetch_current_daily_json(timeout_seconds: int = 20) -> list[dict[str, object]]:
    """Fetch the public TPEx end-of-day mainboard snapshot."""
    request = Request(TPEX_DAILY_QUOTES_URL, headers={"User-Agent": "StockAI-OfflineResearch/1.0 contact: local-user"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout_seconds, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("TPEx response must be a JSON list")
    return payload


def _field(record: dict[str, object], *names: str) -> object:
    for name in names:
        if name in record:
            return record[name]
    return None


def extract_security_names(records: Iterable[dict[str, object]]) -> list[tuple[str, str]]:
    """Pull (symbol, name) pairs out of the same daily snapshot already fetched for bars."""
    entries: list[tuple[str, str]] = []
    for record in records:
        symbol = str(_field(record, "Code", "SecuritiesCompanyCode", "證券代號") or "").strip()
        name = str(_field(record, "CompanyName", "Name", "證券名稱") or "").strip()
        if symbol.isdigit() and len(symbol) == 4 and name:
            entries.append((symbol, name))
    return entries


def parse_daily_records(records: Iterable[dict[str, object]], trading_date: date, published_at: datetime) -> list[DailyBar]:
    """Normalize varied TPEx OpenAPI field names into validated daily bars."""
    if published_at.tzinfo is None:
        raise ValueError("published_at must include a timezone")
    bars: list[DailyBar] = []
    for record in records:
        symbol = str(_field(record, "Code", "SecuritiesCompanyCode", "證券代號") or "").strip()
        if not (symbol.isdigit() and len(symbol) == 4):
            continue
        try:
            bars.append(DailyBar(
                symbol, trading_date,
                _number(_field(record, "Open", "OpeningPrice", "開盤價")),
                _number(_field(record, "High", "HighestPrice", "最高價")),
                _number(_field(record, "Low", "LowestPrice", "最低價")),
                _number(_field(record, "Close", "ClosingPrice", "收盤價")),
                int(_number(_field(record, "TradingShares", "Volume", "TradeVolume", "成交股數"))),
                "TPEX_OPENAPI_MAINBOARD_DAILY_CLOSE_QUOTES", published_at,
            ))
        except ValueError:
            continue
    if not bars:
        raise ValueError("TPEx snapshot yielded no valid four-digit daily bars")
    return bars
