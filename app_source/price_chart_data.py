"""Real, locally-archived daily close prices for one stock, restricted to a
caller-chosen recent window -- feeds the price line chart. Ex-dividend/
ex-rights back-adjusted (dividend_adjustment.py) so a payout doesn't show up
as a fake price cliff, consistent with every other technical calculation in
this app (technical_factor.load_adjusted_bars uses the same adjustment, just
without dates -- this keeps dates since a chart needs them for the x-axis).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from database_utils import database_connection
from dividend_adjustment import adjust_bars, load_adjustment_factors
from historical_storage import DailyBar

WINDOW_CHOICES = {
    "近30天": 30, "近60天": 60, "近180天": 180, "近1年": 365,
}


@dataclass(frozen=True, slots=True)
class DatedClose:
    trading_date: date
    close: float


def load_recent_closes(history_database: Path, symbol: str, window_days: int) -> list[DatedClose]:
    """Real daily closes for `symbol`, restricted to the trailing
    `window_days` calendar days counted back from the symbol's own latest
    locally archived trading date (not today's date -- the app is a
    post-market tool, so "recent" means "recent trading history", and using
    today would show an empty/near-empty chart on a day the market hasn't
    updated yet)."""
    if not history_database.exists():
        return []
    with database_connection(history_database) as connection:
        rows = connection.execute(
            "SELECT trading_date, open_micros, high_micros, low_micros, close_micros, volume "
            "FROM daily_bars WHERE symbol = ? ORDER BY trading_date",
            (symbol,),
        ).fetchall()
    if not rows:
        return []
    now = datetime.now(timezone.utc)
    daily_bars = [
        DailyBar(symbol, date.fromisoformat(d), o / 1_000_000, h / 1_000_000, l / 1_000_000, c / 1_000_000, v, "LOCAL", now)
        for d, o, h, l, c, v in rows
    ]
    events = load_adjustment_factors(history_database, symbol)
    adjusted = adjust_bars(daily_bars, events)
    latest_date = adjusted[-1].trading_date
    cutoff = latest_date - timedelta(days=window_days - 1)
    return [DatedClose(bar.trading_date, bar.close_price) for bar in adjusted if bar.trading_date >= cutoff]
