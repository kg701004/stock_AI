"""Market-wide momentum screener ("選股"): which locally tracked stocks
currently show a golden-cross setup that historically had better-than-coinflip
follow-through.

The rule and its thresholds come directly from scripts/backtest_golden_cross_screen.py,
a research pilot run against real local daily-bar history (see that script's
docstring for full methodology and caveats). On a 300-stock, ~3-year sample:
MA20 crossing above MA60, confirmed by volume (>= 1.2x the 20-day average),
with the 60-day trend itself rising, peer-relative strength positive (own
trailing 30-day return above the cross-sectional median of this same
universe), and minimum liquidity (>= NT$20M/day average turnover) showed a
~59% win rate and +~5% median return over the following 60 trading days.

This is NOT a guarantee -- it is a backtested tendency on a still-partial
slice of the market (only symbols with enough locally archived history
qualify; most of the ~2000-symbol catalog does not yet). Every candidate
returned here should be read as "matched a historically favorable pattern",
not "will go up". Nothing here places any order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean, median

from database_utils import database_connection

MA_SHORT, MA_LONG = 20, 60
VOLUME_BASELINE_WINDOW = 20  # matches technical_layers.calculate's own relative_volume definition
SLOPE_LOOKBACK = 20
RS_LOOKBACK = 30
RECENCY_WINDOW = 5  # a crossover must have happened within this many trading days to still count as "fresh"
MIN_RELATIVE_VOLUME = 1.2
MIN_LIQUIDITY = 20_000_000  # NT$/day, average over VOLUME_BASELINE_WINDOW days
MIN_HISTORY_DAYS = MA_LONG + SLOPE_LOOKBACK + RS_LOOKBACK + 2

Series = tuple[list[str], list[float], list[float]]  # (dates, closes, volumes)


@dataclass(frozen=True, slots=True)
class ScreenerCandidate:
    symbol: str
    signal_date: date
    days_since_signal: int
    relative_volume: float
    ma_long_slope_pct: float
    relative_strength_pct: float
    avg_dollar_volume: float
    price_at_signal: float
    current_price: float
    return_since_signal_pct: float


def _rolling_averages(values: list[float], period: int) -> list[float | None]:
    averages: list[float | None] = [None] * len(values)
    window_sum = 0.0
    for i, value in enumerate(values):
        window_sum += value
        if i >= period:
            window_sum -= values[i - period]
        if i >= period - 1:
            averages[i] = window_sum / period
    return averages


def _load_series(connection, symbol: str) -> Series:
    rows = connection.execute(
        # GROUP BY handles the (symbol, trading_date, source) primary key --
        # a real date can have more than one row (e.g. a regular import and a
        # backfill import); averaging is a safe representative value since
        # duplicate-source rows for the same real trading day should agree.
        "SELECT trading_date, AVG(close_micros), AVG(volume) FROM daily_bars WHERE symbol = ? GROUP BY trading_date ORDER BY trading_date",
        (symbol,),
    ).fetchall()
    dates = [d for d, _micros, _volume in rows]
    closes = [micros / 1_000_000 for _date, micros, _volume in rows]
    volumes = [vol for _date, _micros, vol in rows]
    return dates, closes, volumes


def _peer_median_by_date(series_by_symbol: dict[str, Series]) -> dict[str, float]:
    returns_by_date: dict[str, list[float]] = {}
    for dates, closes, _volumes in series_by_symbol.values():
        for i in range(RS_LOOKBACK, len(closes)):
            if closes[i - RS_LOOKBACK] <= 0:
                continue
            returns_by_date.setdefault(dates[i], []).append(closes[i] / closes[i - RS_LOOKBACK] - 1)
    return {d: median(r) for d, r in returns_by_date.items() if len(r) >= 10}


def scan_market(database: Path) -> list[ScreenerCandidate]:
    """Scan every locally tracked symbol with enough history and return
    those currently matching the validated setup, most recent signal first."""
    if not database.exists():
        return []
    with database_connection(database) as connection:
        symbols = [row[0] for row in connection.execute(
            "SELECT symbol FROM daily_bars GROUP BY symbol HAVING COUNT(DISTINCT trading_date) >= ?",
            (MIN_HISTORY_DAYS,),
        ).fetchall()]
        series_by_symbol = {symbol: _load_series(connection, symbol) for symbol in symbols}

    peer_median_by_date = _peer_median_by_date(series_by_symbol)

    candidates: list[ScreenerCandidate] = []
    for symbol, (dates, closes, volumes) in series_by_symbol.items():
        ma_short = _rolling_averages(closes, MA_SHORT)
        ma_long = _rolling_averages(closes, MA_LONG)
        last_index = len(closes) - 1

        for i in range(max(1, last_index - RECENCY_WINDOW + 1), last_index + 1):
            prev_short, prev_long = ma_short[i - 1], ma_long[i - 1]
            curr_short, curr_long = ma_short[i], ma_long[i]
            if None in (prev_short, prev_long, curr_short, curr_long):
                continue
            if not (prev_short <= prev_long and curr_short > curr_long):
                continue

            baseline_start = i - VOLUME_BASELINE_WINDOW
            if baseline_start < 0:
                continue
            volume_baseline = mean(volumes[baseline_start:i])
            relative_volume = volumes[i] / volume_baseline if volume_baseline else 0.0
            if relative_volume < MIN_RELATIVE_VOLUME:
                continue

            if i - SLOPE_LOOKBACK < 0 or ma_long[i - SLOPE_LOOKBACK] is None:
                continue
            ma_long_slope = curr_long - ma_long[i - SLOPE_LOOKBACK]
            if ma_long_slope <= 0:
                continue

            if i - RS_LOOKBACK < 0 or closes[i - RS_LOOKBACK] <= 0:
                continue
            peer_median = peer_median_by_date.get(dates[i])
            if peer_median is None:
                continue
            relative_strength = (closes[i] / closes[i - RS_LOOKBACK] - 1) - peer_median
            if relative_strength <= 0:
                continue

            dollar_volume_window = range(i - VOLUME_BASELINE_WINDOW + 1, i + 1)
            avg_dollar_volume = mean(closes[j] * volumes[j] for j in dollar_volume_window)
            if avg_dollar_volume < MIN_LIQUIDITY:
                continue

            candidates.append(ScreenerCandidate(
                symbol=symbol,
                signal_date=date.fromisoformat(dates[i]),
                days_since_signal=last_index - i,
                relative_volume=round(relative_volume, 2),
                ma_long_slope_pct=round(ma_long_slope / ma_long[i - SLOPE_LOOKBACK] * 100, 2),
                relative_strength_pct=round(relative_strength * 100, 2),
                avg_dollar_volume=round(avg_dollar_volume, 0),
                price_at_signal=closes[i],
                current_price=closes[last_index],
                return_since_signal_pct=round((closes[last_index] / closes[i] - 1) * 100, 2),
            ))
            break  # one fresh signal per symbol is enough; don't also report older ones in the same window

    candidates.sort(key=lambda c: (c.days_since_signal, -c.relative_strength_pct))
    return candidates
