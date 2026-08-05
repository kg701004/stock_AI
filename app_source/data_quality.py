"""Validation gates for imported factor scores and end-of-day bars."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from historical_storage import DailyBar
from weighted_analysis import AnalysisInput


@dataclass(frozen=True, slots=True)
class QualityReport:
    accepted: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def validate_factor_inputs(items: Iterable[AnalysisInput], as_of: datetime, max_age_days: int = 7) -> QualityReport:
    rows = list(items); errors, warnings = [], []
    seen: set[str] = set()
    for item in rows:
        if item.symbol in seen: errors.append(f"duplicate symbol: {item.symbol}")
        seen.add(item.symbol)
        if item.as_of.tzinfo is None: errors.append(f"timezone missing: {item.symbol}")
        elif (as_of.date() - item.as_of.date()).days > max_age_days: warnings.append(f"stale factor score: {item.symbol}")
    if not rows: errors.append("no factor-score rows")
    return QualityReport(not errors, tuple(errors), tuple(warnings))


def validate_daily_bars(bars: Iterable[DailyBar], expected_date: date | None = None) -> QualityReport:
    rows = list(bars); errors, warnings = [], []
    seen: set[tuple[str, date, str]] = set()
    for bar in rows:
        key = (bar.symbol, bar.trading_date, bar.source)
        if key in seen: errors.append(f"duplicate daily bar: {bar.symbol}")
        seen.add(key)
        if expected_date and bar.trading_date != expected_date: warnings.append(f"unexpected date: {bar.symbol}")
        if not bar.low_price <= min(bar.open_price, bar.close_price) <= bar.high_price: errors.append(f"invalid OHLC: {bar.symbol}")
        if bar.volume < 0: errors.append(f"negative volume: {bar.symbol}")
    if not rows: errors.append("no daily bars")
    return QualityReport(not errors, tuple(errors), tuple(warnings))
