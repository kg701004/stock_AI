"""Backtest preflight checks for locally archived daily-price history.

The check deliberately works without a trading-calendar service: it reports the
actual first/last trading dates and an explicit per-calendar-year bar count.
It never labels a symbol as ten-year ready merely because some rows exist.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from database_utils import database_connection


MIN_BARS_PER_CALENDAR_YEAR = 200


@dataclass(frozen=True, slots=True)
class HistoryCoverage:
    symbol: str
    requested_years: int
    first_date: date | None
    last_date: date | None
    total_bars: int
    yearly_bars: tuple[tuple[int, int], ...]
    missing_years: tuple[int, ...]
    ready_for_backtest: bool
    message: str


def _validate_symbol(symbol: str) -> str:
    normalized = symbol.strip()
    if not (normalized.isdigit() and len(normalized) == 4):
        raise ValueError("symbol must be a four-digit Taiwan stock code")
    return normalized


def check_coverage(database: Path, symbol: str, years: int = 10, as_of: date | None = None) -> HistoryCoverage:
    """Return a conservative history-readiness report for one stock.

    ``years`` is calendar-year based.  A year with fewer than 200 distinct
    daily bars is treated as incomplete, which keeps partial imports from
    silently qualifying a backtest.
    """
    symbol = _validate_symbol(symbol)
    if years < 1:
        raise ValueError("years must be at least one")
    as_of = as_of or date.today()
    # The current calendar year is normally incomplete.  A "ten year" test
    # therefore means ten completed calendar years, not an impossible demand
    # for 200 trading days before year-end.
    target_years = tuple(range(as_of.year - years, as_of.year))
    if not database.exists():
        return HistoryCoverage(symbol, years, None, None, 0, (), target_years, False, "尚未建立歷史資料庫；請先匯入日線資料。")
    try:
        # timeout=30 (via database_connection) lets this wait out a brief
        # write lock from a concurrent historical_backfill.py worker instead
        # of raising immediately -- a raw sqlite3.connect() here previously
        # used the 5s default, and its OperationalError was caught by the
        # same except below as "table not created yet", so a real backfill
        # in progress could misreport as "run an import first".
        with database_connection(database) as connection:
            # DISTINCT matters: daily_bars' primary key is (symbol, trading_date,
            # source), so the same calendar date can legitimately have more than
            # one row when both a regular import and a backfill import (different
            # source strings) cover it. Counting raw rows would double-count
            # those dates and could mislabel a year as complete when it isn't.
            rows = connection.execute(
                "SELECT DISTINCT trading_date FROM daily_bars WHERE symbol = ? ORDER BY trading_date", (symbol,)
            ).fetchall()
    except sqlite3.OperationalError:
        return HistoryCoverage(symbol, years, None, None, 0, (), target_years, False, "歷史資料表尚未建立；請先匯入日線資料。")
    dates = tuple(date.fromisoformat(row[0]) for row in rows)
    counts = {year: 0 for year in target_years}
    for trading_date in dates:
        if trading_date.year in counts:
            counts[trading_date.year] += 1
    yearly_bars = tuple((year, counts[year]) for year in target_years)
    missing = tuple(year for year, count in yearly_bars if count < MIN_BARS_PER_CALENDAR_YEAR)
    first = dates[0] if dates else None
    last = dates[-1] if dates else None
    ready = not missing and first is not None and last is not None
    if ready:
        message = f"已具備最近 {years} 個曆年日線，可進行回測前資料檢核。"
    elif not dates:
        message = "尚無此股日線資料；請匯入標準化 CSV。"
    else:
        # A missing year before the stock's first known trading date isn't a
        # data gap -- it's the stock simply not existing yet (recent IPO/
        # listing), which no amount of re-backfilling can fix. Only years at
        # or after `first` genuinely warrant a "go re-check the data" flag.
        leading = tuple(year for year in missing if year < first.year)
        gaps = tuple(year for year in missing if year not in leading)
        if gaps:
            message = "資料存在缺口，建議重新匯入或回補：" + "、".join(f"{year} 年 {counts[year]} 筆" for year in gaps)
            if leading:
                message += f"；另有 {len(leading)} 個曆年早於資料起始日 {first}（可能上市/上櫃時間較晚，非資料缺口）。"
        else:
            complete_years = years - len(leading)
            message = f"此股票資料起始於 {first}，早於此日期無交易資料（可能上市/上櫃時間較晚而非資料缺口），實際可用完整曆年數：{complete_years}／{years}。"
    return HistoryCoverage(symbol, years, first, last, len(dates), yearly_bars, missing, ready, message)


def check_universe(database: Path, symbols: list[str], years: int = 10, as_of: date | None = None) -> tuple[HistoryCoverage, ...]:
    """Check every selected symbol before a multi-stock backtest is allowed."""
    return tuple(check_coverage(database, symbol, years, as_of) for symbol in symbols)
