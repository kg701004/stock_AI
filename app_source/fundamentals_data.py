"""TWSE public per-stock monthly revenue adapter (real, free, live).

Real data from TWSE's OpenAPI `/opendata/t187ap05_L` -- verified with a live
request (2026-08-01: 1082 listed companies, e.g. 2330 YoY revenue growth
67.87%). Same modern open-data platform as valuation_data.py's BWIBBU_ALL,
not the old MOPS manual-file-import path external_data_importers.py
documents. TWSE-listed (上市) only, same scope caveat as valuation_data.py.
"""
from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen
import certifi

from database_utils import database_connection

TWSE_MONTHLY_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"


@dataclass(frozen=True, slots=True)
class RevenueSnapshot:
    symbol: str
    year_month: str  # ROC "YYYMM", e.g. "11506" -- matches the source field format directly
    year_over_year_growth_pct: float | None


def fetch_current_revenue_json(timeout_seconds: int = 20) -> list[dict[str, object]]:
    request = Request(TWSE_MONTHLY_REVENUE_URL, headers={"User-Agent": "StockAI-OfflineResearch/1.0 contact: local-user"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout_seconds, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("TWSE monthly revenue response must be a JSON list")
    return payload


def _number(value: object) -> float | None:
    text = str(value).replace(",", "").strip()
    if text in {"", "--", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_revenue_records(records: Iterable[dict[str, object]]) -> list[RevenueSnapshot]:
    snapshots: list[RevenueSnapshot] = []
    for record in records:
        symbol = str(record.get("公司代號", "")).strip()
        if not (symbol.isdigit() and len(symbol) == 4):
            continue
        year_month = str(record.get("資料年月", "")).strip()
        if not year_month:
            continue
        growth = _number(record.get("營業收入-去年同月增減(%)"))
        snapshots.append(RevenueSnapshot(symbol, year_month, growth))
    return snapshots


def ensure_schema(connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS revenue_snapshots (
            symbol TEXT NOT NULL, year_month TEXT NOT NULL,
            year_over_year_growth_pct REAL, imported_at TEXT NOT NULL,
            PRIMARY KEY (symbol, year_month)
        )
    """)


def store_revenue_snapshots(database: Path, snapshots: Iterable[RevenueSnapshot]) -> int:
    rows = list(snapshots)
    if not rows:
        return 0
    database.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone().isoformat()
    with database_connection(database) as connection:
        ensure_schema(connection)
        connection.executemany(
            "INSERT OR REPLACE INTO revenue_snapshots VALUES (?, ?, ?, ?)",
            [(s.symbol, s.year_month, s.year_over_year_growth_pct, now) for s in rows],
        )
    return len(rows)


def update_revenue_snapshots(database: Path, timeout_seconds: int = 20) -> int:
    return store_revenue_snapshots(database, parse_revenue_records(fetch_current_revenue_json(timeout_seconds)))


def latest_revenue(database: Path, symbol: str) -> RevenueSnapshot | None:
    if not database.exists():
        return None
    with database_connection(database) as connection:
        ensure_schema(connection)
        row = connection.execute(
            "SELECT symbol, year_month, year_over_year_growth_pct FROM revenue_snapshots "
            "WHERE symbol = ? ORDER BY year_month DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    return None if row is None else RevenueSnapshot(row[0], row[1], row[2])


def fundamentals_score_from_snapshot(snapshot: RevenueSnapshot) -> float | None:
    """Higher YoY monthly revenue growth scores higher. Anchors: -20% or
    worse -> 15 (meaningful decline), +30% or better -> 85 (strong growth),
    linear between, 0% growth centers near neutral (~50). Simple, disclosed
    heuristic on a single real metric (revenue trend), not a full fundamental
    analysis -- profitability, margins, debt are not in this API response."""
    if snapshot.year_over_year_growth_pct is None:
        return None
    low_growth, low_score = -20.0, 15.0
    high_growth, high_score = 30.0, 85.0
    ratio = (snapshot.year_over_year_growth_pct - low_growth) / (high_growth - low_growth)
    return round(max(0.0, min(100.0, low_score + ratio * (high_score - low_score))), 1)


def fundamentals_factor_score(database: Path, symbol: str) -> tuple[float | None, str]:
    """Return (score, note); score is None when there's no local revenue snapshot yet."""
    snapshot = latest_revenue(database, symbol)
    if snapshot is None:
        return None, "尚無本機營收資料（僅上市（TWSE）股票有提供），無法自動建議基本面分數。"
    score = fundamentals_score_from_snapshot(snapshot)
    if score is None:
        return None, f"{snapshot.year_month} 月營收年增率缺值，無法自動建議基本面分數。"
    return score, f"依 {snapshot.year_month}（民國年月）月營收年增率 {snapshot.year_over_year_growth_pct:+.2f}% 自動建議（僅反映營收單一指標，非完整財務分析；僅供參考，可自行調整）。"
