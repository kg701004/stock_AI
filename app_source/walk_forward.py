"""Small deterministic walk-forward evaluator; no future prices are used in a signal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ScoreObservation:
    date: str
    symbol: str
    score: float
    close: float


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    trades: int
    hit_rate_pct: float
    average_return_pct: float
    max_drawdown_pct: float


def evaluate(observations: Iterable[ScoreObservation], minimum_score: float = 60) -> WalkForwardResult:
    grouped: dict[str, list[ScoreObservation]] = {}
    for row in observations: grouped.setdefault(row.symbol, []).append(row)
    returns: list[float] = []
    for rows in grouped.values():
        rows.sort(key=lambda row: row.date)
        for current, future in zip(rows, rows[1:]):
            if current.score >= minimum_score and current.close > 0: returns.append((future.close / current.close - 1) * 100)
    equity, peak, drawdown = 1.0, 1.0, 0.0
    for result in returns:
        equity *= 1 + result / 100; peak = max(peak, equity); drawdown = min(drawdown, (equity / peak - 1) * 100)
    return WalkForwardResult(len(returns), round(sum(x > 0 for x in returns) / len(returns) * 100, 2) if returns else 0, round(sum(returns) / len(returns), 2) if returns else 0, round(drawdown, 2))
