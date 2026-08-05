"""TWSE public end-of-day data adapter.

This module only fetches public, end-of-day data.  It has no broker account,
credential, WebSocket, order, or real-time capability.  Network fetching is
kept separate from parsing so the data contract can be tested offline.
"""

from __future__ import annotations

import csv
import json
import ssl
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen
import certifi

from historical_storage import DailyBar


TWSE_STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_LEGACY_STOCK_DAY_ALL_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json"

def _read_json(url: str, timeout_seconds: int) -> object:
    request = Request(url, headers={"User-Agent": "StockAI-OfflineResearch/1.0 contact: local-user"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout_seconds, context=context) as response:
        return json.loads(response.read().decode("utf-8"))

def _legacy_to_openapi(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("fields"), list) or not isinstance(payload.get("data"), list):
        raise ValueError("TWSE legacy response shape is invalid")
    fields = {str(name): index for index, name in enumerate(payload["fields"])}
    names = {"證券代號":"Code","開盤價":"OpeningPrice","最高價":"HighestPrice","最低價":"LowestPrice","收盤價":"ClosingPrice","成交股數":"TradeVolume"}
    if not all(name in fields for name in names): raise ValueError("TWSE legacy response fields are incomplete")
    return [{target: row[fields[name]] for name, target in names.items()} for row in payload["data"] if isinstance(row, list)]


def fetch_current_daily_json(timeout_seconds: int = 20) -> list[dict[str, object]]:
    """Fetch the current public TWSE end-of-day snapshot with a clear user agent."""
    try:
        payload = _read_json(TWSE_STOCK_DAY_ALL_URL, timeout_seconds)
        if not isinstance(payload, list): raise ValueError("TWSE OpenAPI response is not a JSON list")
        return payload
    except Exception as primary_error:
        try: return _legacy_to_openapi(_read_json(TWSE_LEGACY_STOCK_DAY_ALL_URL, timeout_seconds))
        except Exception as fallback_error: raise RuntimeError(f"TWSE primary and official fallback both failed: {primary_error}; {fallback_error}") from fallback_error


def _number(value: object) -> float:
    text = str(value).replace(",", "").strip()
    if text in {"", "--", "-"}:
        raise ValueError("missing numeric value")
    return float(text)


def parse_daily_records(records: Iterable[dict[str, object]], trading_date: date, published_at: datetime) -> list[DailyBar]:
    """Normalize a TWSE daily snapshot into validated internal daily bars."""
    if published_at.tzinfo is None:
        raise ValueError("published_at must include a timezone")
    bars: list[DailyBar] = []
    for record in records:
        symbol = str(record.get("Code", record.get("證券代號", ""))).strip()
        if not (symbol.isdigit() and len(symbol) == 4):
            continue  # Skip malformed/non-four-digit instruments; asset filtering is a separate module.
        try:
            bars.append(DailyBar(
                symbol=symbol,
                trading_date=trading_date,
                open_price=_number(record.get("OpeningPrice", record.get("開盤價"))),
                high_price=_number(record.get("HighestPrice", record.get("最高價"))),
                low_price=_number(record.get("LowestPrice", record.get("最低價"))),
                close_price=_number(record.get("ClosingPrice", record.get("收盤價"))),
                volume=int(_number(record.get("TradeVolume", record.get("成交股數")))),
                source="TWSE_OPENAPI_STOCK_DAY_ALL",
                published_at=published_at,
            ))
        except ValueError:
            continue  # Incomplete instruments are not silently fabricated.
    if not bars:
        raise ValueError("TWSE snapshot yielded no valid four-digit stock daily bars")
    return bars


def extract_security_names(records: Iterable[dict[str, object]]) -> list[tuple[str, str]]:
    """Pull (symbol, name) pairs out of the same daily snapshot already fetched for bars."""
    entries: list[tuple[str, str]] = []
    for record in records:
        symbol = str(record.get("Code", record.get("證券代號", ""))).strip()
        name = str(record.get("Name", record.get("證券名稱", ""))).strip()
        if symbol.isdigit() and len(symbol) == 4 and name:
            entries.append((symbol, name))
    return entries


def write_normalized_csv(bars: Iterable[DailyBar], output_path: Path) -> None:
    """Write standard daily-bar CSV suitable for history_cli.py import."""
    rows = list(bars)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "date", "open", "high", "low", "close", "volume", "source", "published_at"])
        writer.writeheader()
        writer.writerows({"symbol": row.symbol, "date": row.trading_date.isoformat(), "open": row.open_price, "high": row.high_price, "low": row.low_price, "close": row.close_price, "volume": row.volume, "source": row.source, "published_at": row.published_at.isoformat()} for row in rows)
