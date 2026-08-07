"""Market-wide relative-strength (momentum) screener ("動量排名"): which
locally tracked stocks currently sit in the top of the cross-sectional
trailing-return distribution -- a distinct dimension from stock_screener.py's
golden-cross screen (that one requires a specific chart pattern; this one is
purely "has this stock outperformed the rest of the liquid, tracked market
over the last 60 trading days").

The rule and its thresholds come directly from
scripts/backtest_momentum_ranking.py, a research pilot run against real
local daily-bar history (see that script's docstring for full methodology
and caveats). On a 1,271-symbol universe (>=750 trading days locally,
liquidity floor NT$20M/day average dollar volume), ranking by trailing
60-trading-day return and holding the top quintile for 60 trading days
showed, out-of-sample: 47.23% positive, mean +7.24% return (stdev 36.97%),
vs the bottom quintile's 46.14% positive, mean +2.01% (stdev 22.38%) -- a
+5.23 percentage-point spread, Welch's t=23.55 (n≈38,000 each side).

This is a population-level average tilt, not a per-stock guarantee -- the
standard deviation is large relative to the mean, so plenty of individual
top-quintile stocks still lose money over any given 60-day window. Nothing
here places any order. An earlier, smaller pilot (50 symbols with the
single deepest local history) showed a similar but less rigorously
supported effect; this module's thresholds come from the larger, liquidity-
filtered re-run, not that first pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean

from database_utils import database_connection

MIN_HISTORY_DAYS = 750
LOOKBACK_DAYS = 60
VOLUME_BASELINE_WINDOW = 20  # matches stock_screener.py's own liquidity-window convention
MIN_LIQUIDITY = 20_000_000  # NT$/day, average over VOLUME_BASELINE_WINDOW days -- same floor as stock_screener.py
TOP_QUINTILE_FRACTION = 0.2


@dataclass(frozen=True, slots=True)
class MomentumCandidate:
    symbol: str
    as_of_date: date
    trailing_return_pct: float
    percentile_rank: float  # 0-100; 100 = strongest momentum in today's liquid universe
    avg_dollar_volume: float
    current_price: float


def _load_recent_series(connection, symbol: str, min_rows: int) -> tuple[list[str], list[float], list[float]] | None:
    rows = connection.execute(
        "SELECT trading_date, AVG(close_micros), AVG(volume) FROM daily_bars WHERE symbol = ? GROUP BY trading_date ORDER BY trading_date DESC LIMIT ?",
        (symbol, min_rows),
    ).fetchall()
    if len(rows) < min_rows:
        return None
    rows.reverse()  # back to chronological order
    dates = [r[0] for r in rows]
    closes = [r[1] / 1_000_000 for r in rows]
    volumes = [r[2] for r in rows]
    return dates, closes, volumes


def scan_momentum_leaders(database: Path) -> list[MomentumCandidate]:
    """Scan every locally tracked, sufficiently-long-history, liquid symbol
    and return those currently in the top quintile of trailing 60-trading-day
    return, strongest first."""
    if not database.exists():
        return []
    required_rows = LOOKBACK_DAYS + 1
    with database_connection(database) as connection:
        symbols = [row[0] for row in connection.execute(
            "SELECT symbol FROM daily_bars GROUP BY symbol HAVING COUNT(DISTINCT trading_date) >= ?",
            (MIN_HISTORY_DAYS,),
        ).fetchall()]

        universe: list[tuple[str, str, float, float, float]] = []  # symbol, as_of_date, trailing_return, avg_dollar_volume, current_price
        for symbol in symbols:
            series = _load_recent_series(connection, symbol, required_rows)
            if series is None:
                continue
            dates, closes, volumes = series
            if closes[0] <= 0:
                continue
            trailing_return = closes[-1] / closes[0] - 1
            avg_dollar_volume = mean(closes[i] * volumes[i] for i in range(len(closes) - VOLUME_BASELINE_WINDOW, len(closes)))
            if avg_dollar_volume < MIN_LIQUIDITY:
                continue
            universe.append((symbol, dates[-1], trailing_return, avg_dollar_volume, closes[-1]))

    if len(universe) < 40:
        # Too small a liquid universe for a percentile ranking to mean anything;
        # matches the >= 40 floor used during the validating backtest.
        return []

    universe.sort(key=lambda row: row[2])
    n = len(universe)
    cutoff_index = n - max(1, int(n * TOP_QUINTILE_FRACTION))
    top = universe[cutoff_index:]

    candidates = [
        MomentumCandidate(
            symbol=symbol,
            as_of_date=date.fromisoformat(as_of_date),
            trailing_return_pct=round(trailing_return * 100, 2),
            percentile_rank=round((cutoff_index + rank) / n * 100, 1),
            avg_dollar_volume=round(avg_dollar_volume, 0),
            current_price=current_price,
        )
        for rank, (symbol, as_of_date, trailing_return, avg_dollar_volume, current_price) in enumerate(top)
    ]
    candidates.sort(key=lambda c: -c.trailing_return_pct)
    return candidates
