"""Derives the `technical` factor score straight from locally archived daily bars.

Technical is the one weighted_analysis factor that never needs manual input:
once a symbol has enough daily-bar history (already collected by the routine
TWSE/TPEx updates), its score can be computed automatically instead of being
typed in by hand.

Prices are back-adjusted for ex-dividend/ex-rights events (dividend_adjustment.py)
before any moving-average/support/resistance math -- otherwise a stock's own
ex-dividend date shows up as a fake price gap and corrupts the signal, which
matters a lot for Taiwan's high-dividend-yield stocks and ETFs.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from pathlib import Path

from database_utils import database_connection
from dividend_adjustment import adjust_bars, load_adjustment_factors
from historical_storage import DailyBar, average_daily_trading_value
from technical_layers import Bar
from technical_signal import calculate as calculate_technical_signal

MINIMUM_BARS = 21


def load_adjusted_bars(history_database: Path, symbol: str) -> list[Bar]:
    """Ex-dividend/ex-rights back-adjusted Bar series for one symbol; reused by every technical consumer."""
    with database_connection(history_database) as connection:
        rows = connection.execute(
            "SELECT trading_date, open_micros, high_micros, low_micros, close_micros, volume "
            "FROM daily_bars WHERE symbol = ? ORDER BY trading_date",
            (symbol,),
        ).fetchall()
    placeholder_now = datetime.now(timezone.utc)
    daily_bars = [
        DailyBar(symbol, date.fromisoformat(trading_date), open_ / 1_000_000, high / 1_000_000, low / 1_000_000, close / 1_000_000, volume, "LOCAL", placeholder_now)
        for trading_date, open_, high, low, close, volume in rows
    ]
    events = load_adjustment_factors(history_database, symbol)
    adjusted = adjust_bars(daily_bars, events)
    return [Bar(bar.close_price, bar.high_price, bar.low_price, bar.volume) for bar in adjusted]


def technical_factor_score(history_database: Path, symbol: str) -> tuple[float | None, str]:
    """Return (score, note); score is None when there isn't enough history yet."""
    if not history_database.exists():
        return None, "尚無本機歷史資料，無法自動計算技術面分數。"
    try:
        bars = load_adjusted_bars(history_database, symbol)
    except Exception:
        return None, "尚無本機歷史資料，無法自動計算技術面分數。"
    if len(bars) < MINIMUM_BARS:
        return None, f"歷史資料僅 {len(bars)} 筆，少於自動計算所需的 {MINIMUM_BARS} 筆，暫不提供技術面分數。"
    signal = calculate_technical_signal(bars)
    note = signal.status + ("；" + "；".join(signal.reasons) if signal.reasons else "")
    return signal.score, note


def liquidity_score_from_avg_daily_value(avg_daily_value_ntd: float) -> float:
    """Map average daily trading value (NT$, close * volume) to a 0-100
    liquidity factor score (higher = easier to trade in/out of size).

    Log-scale anchors since trading value spans orders of magnitude across
    the market: NT$3,000,000/day (thin, hard to move size) -> 20;
    NT$500,000,000/day (blue-chip level liquidity) -> 90. A simple, disclosed
    heuristic the user can still override, not a cross-sectional ranking
    against the rest of the market (this app has no such ranking data).
    """
    if avg_daily_value_ntd <= 0:
        return 0.0
    low_value, low_score = 3_000_000.0, 20.0
    high_value, high_score = 500_000_000.0, 90.0
    ratio = (math.log10(avg_daily_value_ntd) - math.log10(low_value)) / (math.log10(high_value) - math.log10(low_value))
    return round(max(0.0, min(100.0, low_score + ratio * (high_score - low_score))), 1)


def liquidity_factor_score(history_database: Path, symbol: str) -> tuple[float | None, str]:
    """Return (score, note); score is None when there's no local daily-bar history yet."""
    avg_value = average_daily_trading_value(history_database, symbol)
    if avg_value is None:
        return None, "尚無本機歷史資料，無法自動建議流動性分數。"
    score = liquidity_score_from_avg_daily_value(avg_value)
    return score, f"依最近 20 個交易日平均成交金額約 {avg_value:,.0f} 元自動建議（僅供參考，可自行調整）。"
