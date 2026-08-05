"""TWSE public per-stock valuation (P/E, dividend yield, P/B) adapter.

Real, free, live data from TWSE's OpenAPI `BWIBBU_ALL` -- verified with a
live request (2026-07-31: 1081 listed stocks, e.g. 2330 PE=32.60,
DividendYield=0.91%, PB=10.67). This is a *different*, modern open-data
platform from the old MOPS web-query flow that external_data_importers.py
documents as requiring a manually downloaded file; that limitation does not
apply here. TWSE-listed (上市) only -- BWIBBU_ALL has no TPEx (上櫃)
equivalent found yet, so a TPEx symbol honestly falls back to no valuation
score rather than a TWSE-sourced guess.
"""
from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen
import certifi

from database_utils import database_connection

TWSE_VALUATION_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"


@dataclass(frozen=True, slots=True)
class ValuationSnapshot:
    symbol: str
    trading_date: date
    pe_ratio: float | None
    dividend_yield_pct: float | None
    pb_ratio: float | None


def fetch_current_valuation_json(timeout_seconds: int = 20) -> list[dict[str, object]]:
    request = Request(TWSE_VALUATION_URL, headers={"User-Agent": "StockAI-OfflineResearch/1.0 contact: local-user"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout_seconds, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("TWSE valuation response must be a JSON list")
    return payload


def _number(value: object) -> float | None:
    text = str(value).replace(",", "").strip()
    if text in {"", "--", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_valuation_records(records: Iterable[dict[str, object]]) -> list[ValuationSnapshot]:
    """TWSE's 'Date' field here is a plain ROC date string, e.g. '1150731'."""
    snapshots: list[ValuationSnapshot] = []
    for record in records:
        symbol = str(record.get("Code", "")).strip()
        if not (symbol.isdigit() and len(symbol) == 4):
            continue
        date_text = str(record.get("Date", "")).strip()
        try:
            roc_year, month, day = int(date_text[:-4]), int(date_text[-4:-2]), int(date_text[-2:])
            trading_date = date(roc_year + 1911, month, day)
        except (ValueError, IndexError):
            continue
        snapshots.append(ValuationSnapshot(
            symbol, trading_date,
            _number(record.get("PEratio")), _number(record.get("DividendYield")), _number(record.get("PBratio")),
        ))
    return snapshots


def ensure_schema(connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS valuation_snapshots (
            symbol TEXT NOT NULL, trading_date TEXT NOT NULL,
            pe_ratio REAL, dividend_yield_pct REAL, pb_ratio REAL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY (symbol, trading_date)
        )
    """)


def store_valuation_snapshots(database: Path, snapshots: Iterable[ValuationSnapshot]) -> int:
    rows = list(snapshots)
    if not rows:
        return 0
    database.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone().isoformat()
    with database_connection(database) as connection:
        ensure_schema(connection)
        connection.executemany(
            "INSERT OR REPLACE INTO valuation_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            [(s.symbol, s.trading_date.isoformat(), s.pe_ratio, s.dividend_yield_pct, s.pb_ratio, now) for s in rows],
        )
    return len(rows)


def update_valuation_snapshots(database: Path, timeout_seconds: int = 20) -> int:
    """Fetch + store in one call; the daily-update entry point uses this."""
    return store_valuation_snapshots(database, parse_valuation_records(fetch_current_valuation_json(timeout_seconds)))


def latest_valuation(database: Path, symbol: str) -> ValuationSnapshot | None:
    if not database.exists():
        return None
    with database_connection(database) as connection:
        ensure_schema(connection)
        row = connection.execute(
            "SELECT symbol, trading_date, pe_ratio, dividend_yield_pct, pb_ratio FROM valuation_snapshots "
            "WHERE symbol = ? ORDER BY trading_date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    if row is None:
        return None
    return ValuationSnapshot(row[0], date.fromisoformat(row[1]), row[2], row[3], row[4])


def _pe_subscore(pe_ratio: float | None) -> float | None:
    """Lower P/E scores higher (cheaper). Loss-making (P/E<=0) or missing ->
    None rather than guessing: a negative/undefined P/E says nothing about
    cheap-vs-expensive on this simple scale."""
    if pe_ratio is None or pe_ratio <= 0:
        return None
    low_pe, low_score = 10.0, 85.0
    high_pe, high_score = 40.0, 25.0
    ratio = (pe_ratio - low_pe) / (high_pe - low_pe)
    return round(max(0.0, min(100.0, low_score + ratio * (high_score - low_score))), 1)


def _dividend_yield_subscore(dividend_yield_pct: float | None) -> float | None:
    """Higher yield scores higher (more income)."""
    if dividend_yield_pct is None:
        return None
    low_yield, low_score = 0.0, 30.0
    high_yield, high_score = 6.0, 85.0
    ratio = (dividend_yield_pct - low_yield) / (high_yield - low_yield)
    return round(max(0.0, min(100.0, low_score + ratio * (high_score - low_score))), 1)


def _pb_subscore(pb_ratio: float | None) -> float | None:
    """Lower P/B scores higher (trading closer to/below book value)."""
    if pb_ratio is None or pb_ratio <= 0:
        return None
    low_pb, low_score = 1.0, 85.0
    high_pb, high_score = 5.0, 25.0
    ratio = (pb_ratio - low_pb) / (high_pb - low_pb)
    return round(max(0.0, min(100.0, low_score + ratio * (high_score - low_score))), 1)


def valuation_score_from_snapshot(snapshot: ValuationSnapshot) -> float | None:
    """Average of whichever of the three (P/E, yield, P/B) sub-scores are
    available; None only when none of the three are usable. Simple, disclosed
    heuristic anchors (see the three _*_subscore docstrings) -- not a
    statistically fitted model, same honesty caveat as global_risk/liquidity."""
    subscores = [s for s in (
        _pe_subscore(snapshot.pe_ratio),
        _dividend_yield_subscore(snapshot.dividend_yield_pct),
        _pb_subscore(snapshot.pb_ratio),
    ) if s is not None]
    if not subscores:
        return None
    return round(sum(subscores) / len(subscores), 1)


def valuation_factor_score(database: Path, symbol: str) -> tuple[float | None, str]:
    """Return (score, note); score is None when there's no local valuation snapshot yet."""
    snapshot = latest_valuation(database, symbol)
    if snapshot is None:
        return None, "尚無本機評價資料（本益比／殖利率／股價淨值比僅上市（TWSE）股票有提供），無法自動建議評價分數。"
    score = valuation_score_from_snapshot(snapshot)
    if score is None:
        return None, f"{snapshot.trading_date} 的本益比／殖利率／股價淨值比皆缺值或不適用（例如虧損無本益比），無法自動建議評價分數。"
    parts = []
    if snapshot.pe_ratio is not None:
        parts.append(f"本益比 {snapshot.pe_ratio:.2f}")
    if snapshot.dividend_yield_pct is not None:
        parts.append(f"殖利率 {snapshot.dividend_yield_pct:.2f}%")
    if snapshot.pb_ratio is not None:
        parts.append(f"股價淨值比 {snapshot.pb_ratio:.2f}")
    return score, f"依 {snapshot.trading_date}｜" + "、".join(parts) + " 自動建議（僅供參考，可自行調整）。"
