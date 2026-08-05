"""Offline market-context scoring for public, end-of-day data.

The inputs are intentionally normalized snapshots.  A later public-data
connector can populate them from TWSE, TPEx, TAIFEX, SEC, or manually imported
CSV files without changing the analysis logic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """End-of-day Taiwan and international risk inputs, all in percent units."""

    advancers: int
    decliners: int
    new_highs: int
    new_lows: int
    above_20ma: int
    universe_size: int
    twse_return_pct: float
    tpex_return_pct: float
    foreign_market_return_pct: float | None = None
    volatility_index: float | None = None
    put_call_open_interest_ratio: float | None = None

    def __post_init__(self) -> None:
        if self.universe_size <= 0:
            raise ValueError("universe_size must be positive")
        for value in (self.advancers, self.decliners, self.new_highs, self.new_lows, self.above_20ma):
            if value < 0:
                raise ValueError("breadth counts cannot be negative")


@dataclass(frozen=True, slots=True)
class MarketAssessment:
    regime: str
    risk_level: str
    breadth_ratio: float
    score: int
    reasons: tuple[str, ...]


def assess_market(snapshot: MarketSnapshot) -> MarketAssessment:
    """Produce explainable end-of-day risk context; this is not a trade order."""
    breadth_ratio = snapshot.advancers / max(1, snapshot.advancers + snapshot.decliners)
    score = 50
    reasons: list[str] = []
    above_20ma_ratio = snapshot.above_20ma / snapshot.universe_size

    if breadth_ratio >= 0.6 and above_20ma_ratio >= 0.6:
        score += 25
        reasons.append("market breadth and 20-day participation are positive")
    elif breadth_ratio <= 0.4 and above_20ma_ratio <= 0.4:
        score -= 25
        reasons.append("market breadth and 20-day participation are weak")

    if snapshot.new_highs > snapshot.new_lows * 2:
        score += 10
        reasons.append("new highs materially exceed new lows")
    elif snapshot.new_lows > snapshot.new_highs * 2:
        score -= 10
        reasons.append("new lows materially exceed new highs")

    if snapshot.twse_return_pct <= -1.5 and snapshot.tpex_return_pct <= -1.5:
        score -= 15
        reasons.append("listed and OTC markets declined sharply together")
    elif snapshot.twse_return_pct >= 1 and snapshot.tpex_return_pct >= 1:
        score += 10
        reasons.append("listed and OTC markets rose together")

    if snapshot.foreign_market_return_pct is not None and snapshot.foreign_market_return_pct <= -1.5:
        score -= 10
        reasons.append("foreign-market risk overlay is negative")
    if snapshot.volatility_index is not None and snapshot.volatility_index >= 25:
        score -= 10
        reasons.append("volatility index is elevated")
    if snapshot.put_call_open_interest_ratio is not None and snapshot.put_call_open_interest_ratio >= 1.3:
        score -= 5
        reasons.append("put/call open-interest ratio is elevated")

    score = max(0, min(100, score))
    regime = "bullish" if score >= 65 else "bearish" if score <= 35 else "range_bound"
    risk_level = "high" if score <= 35 else "moderate" if score < 65 else "normal"
    return MarketAssessment(regime, risk_level, round(breadth_ratio, 4), score, tuple(reasons))
