"""Market breadth (全市場漲跌家數) and sector relative strength (產業輪動),
computed entirely from data already collected locally by the routine daily
updates -- no new external fetch needed, unlike valuation_data.py/
fundamentals_data.py. Both compare the two most recent distinct trading
dates already in `daily_bars`, so this is a real but short-horizon (1-day)
signal, not a multi-week trend -- disclosed explicitly rather than implying
more sophistication than it has.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from database_utils import database_connection


@dataclass(frozen=True, slots=True)
class BreadthSnapshot:
    trading_date: str
    previous_date: str
    advancing: int
    declining: int
    unchanged: int


def _two_most_recent_dates(connection) -> tuple[str, str] | None:
    rows = connection.execute("SELECT DISTINCT trading_date FROM daily_bars ORDER BY trading_date DESC LIMIT 2").fetchall()
    if len(rows) < 2:
        return None
    return rows[0][0], rows[1][0]


def compute_market_breadth(database: Path) -> BreadthSnapshot | None:
    """Advancing/declining/unchanged counts across every symbol present on
    both of the two most recent locally archived trading dates."""
    if not database.exists():
        return None
    with database_connection(database) as connection:
        dates = _two_most_recent_dates(connection)
        if dates is None:
            return None
        latest_date, previous_date = dates
        rows = connection.execute(
            """
            SELECT l.close_micros, p.close_micros
            FROM daily_bars l JOIN daily_bars p ON l.symbol = p.symbol
            WHERE l.trading_date = ? AND p.trading_date = ?
            """,
            (latest_date, previous_date),
        ).fetchall()
    advancing = sum(1 for latest, previous in rows if latest > previous)
    declining = sum(1 for latest, previous in rows if latest < previous)
    unchanged = len(rows) - advancing - declining
    return BreadthSnapshot(latest_date, previous_date, advancing, declining, unchanged)


def market_breadth_score_from_snapshot(snapshot: BreadthSnapshot) -> float | None:
    """Higher advancing ratio scores higher. Anchors: 30% advancing -> 20
    (broadly weak), 70% advancing -> 80 (broadly strong), linear between."""
    total = snapshot.advancing + snapshot.declining
    if total == 0:
        return None
    advancing_ratio = snapshot.advancing / total
    low_ratio, low_score = 0.30, 20.0
    high_ratio, high_score = 0.70, 80.0
    ratio = (advancing_ratio - low_ratio) / (high_ratio - low_ratio)
    return round(max(0.0, min(100.0, low_score + ratio * (high_score - low_score))), 1)


def market_breadth_factor_score(database: Path) -> tuple[float | None, str]:
    """Market-wide, same value applied to every symbol's "market_breadth"
    factor -- same convention as global_risk_factor_score's VIX-based score."""
    snapshot = compute_market_breadth(database)
    if snapshot is None:
        return None, "本機歷史資料不足兩個交易日，無法自動建議市場廣度分數。"
    total = snapshot.advancing + snapshot.declining
    if total == 0:
        return None, "本機沒有可比較漲跌的股票資料，無法自動建議市場廣度分數。"
    score = market_breadth_score_from_snapshot(snapshot)
    return score, (
        f"依 {snapshot.previous_date} → {snapshot.trading_date} 全市場漲跌家數（上漲 {snapshot.advancing}、"
        f"下跌 {snapshot.declining}、持平 {snapshot.unchanged}）自動建議（僅反映最近一個交易日，非長期趨勢；僅供參考，可自行調整）。"
    )


def compute_sector_relative_return(database: Path, symbol: str) -> tuple[float, float, str] | None:
    """Return (sector_avg_return_pct, market_avg_return_pct, sector_name) for
    this symbol's sector vs the whole market, over the same two most recent
    trading dates. None if the symbol has no catalogued sector, or there
    isn't enough data."""
    if not database.exists():
        return None
    with database_connection(database) as connection:
        try:
            sector_row = connection.execute("SELECT sector FROM securities WHERE symbol = ?", (symbol,)).fetchone()
        except sqlite3.OperationalError:
            return None  # securities catalog not created yet -- no sector data to work with
        if sector_row is None or not sector_row[0]:
            return None
        sector = sector_row[0]
        dates = _two_most_recent_dates(connection)
        if dates is None:
            return None
        latest_date, previous_date = dates
        rows = connection.execute(
            """
            SELECT s.sector, l.close_micros, p.close_micros
            FROM daily_bars l
            JOIN daily_bars p ON l.symbol = p.symbol
            JOIN securities s ON s.symbol = l.symbol
            WHERE l.trading_date = ? AND p.trading_date = ? AND p.close_micros > 0
            """,
            (latest_date, previous_date),
        ).fetchall()
    if not rows:
        return None
    market_returns = [(latest - previous) / previous for _sector, latest, previous in rows]
    sector_returns = [(latest - previous) / previous for row_sector, latest, previous in rows if row_sector == sector]
    if not sector_returns:
        return None
    return round(sum(sector_returns) / len(sector_returns) * 100, 3), round(sum(market_returns) / len(market_returns) * 100, 3), sector


def sector_rotation_score_from_returns(sector_return_pct: float, market_return_pct: float) -> float:
    """Higher relative outperformance (sector return - market return) scores
    higher. Anchors: -2 percentage points -> 20 (sector lagging), +2pp -> 80
    (sector leading), linear between."""
    spread = sector_return_pct - market_return_pct
    low_spread, low_score = -2.0, 20.0
    high_spread, high_score = 2.0, 80.0
    ratio = (spread - low_spread) / (high_spread - low_spread)
    return round(max(0.0, min(100.0, low_score + ratio * (high_score - low_score))), 1)


def sector_rotation_factor_score(database: Path, symbol: str) -> tuple[float | None, str]:
    result = compute_sector_relative_return(database, symbol)
    if result is None:
        return None, "本機無此股票的產業分類或近兩個交易日資料，無法自動建議產業輪動分數。"
    sector_return, market_return, sector = result
    score = sector_rotation_score_from_returns(sector_return, market_return)
    return score, f"「{sector}」最近一個交易日平均報酬 {sector_return:+.2f}%，全市場平均 {market_return:+.2f}% 自動建議（僅反映最近一個交易日，非長期產業趨勢；僅供參考，可自行調整）。"
