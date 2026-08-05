"""Short-term reversal signal module.

Checks if the latest bar has dropped significantly over a short lookback window,
using ex-dividend/ex-rights back-adjusted bars.
"""
from __future__ import annotations

from pathlib import Path
from technical_layers import Bar
from technical_factor import load_adjusted_bars


def short_term_reversal_signal(bars: list[Bar], lookback: int = 5, drop_pct: float = 8.0) -> bool:
    """Checks if the close price of the latest bar has dropped by at least drop_pct%
    relative to the close price of the bar lookback days ago.

    Returns False if there is insufficient data (len(bars) < lookback + 1),
    without raising an exception.
    """
    if len(bars) < lookback + 1:
        return False

    price_old = bars[-1 - lookback].close
    price_new = bars[-1].close

    if price_old <= 0:
        return False

    calculated_drop = (price_old - price_new) / price_old * 100.0
    return calculated_drop >= drop_pct


def calculate_short_term_reversal_for_symbol(
    history_database: Path, symbol: str, lookback: int = 5, drop_pct: float = 8.0
) -> bool:
    """Helper to check short-term reversal signal for a given symbol from database."""
    if not history_database.exists():
        return False
    try:
        bars = load_adjusted_bars(history_database, symbol)
    except Exception:
        return False
    return short_term_reversal_signal(bars, lookback, drop_pct)
