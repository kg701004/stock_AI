"""Offline proof of concept for Taiwan-stock analysis components.

This module intentionally uses no network, broker SDK, or third-party package.
It validates the data flow with deterministic mock ticks.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Iterable

TAIPEI = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class Tick:
    symbol: str
    exchange_time: datetime
    received_time: datetime
    price: float
    volume: int

    def __post_init__(self) -> None:
        if not self.symbol.isdigit() or len(self.symbol) != 4:
            raise ValueError("symbol must be a four-digit Taiwan stock code")
        if self.price <= 0 or self.volume < 0:
            raise ValueError("price must be positive and volume non-negative")


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    start_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


def generate_mock_ticks(
    symbol: str, start: datetime, count: int, seed: int = 7
) -> list[Tick]:
    """Return deterministic minute ticks for offline development and testing."""
    rng = random.Random(seed)
    price = 100.0
    ticks: list[Tick] = []
    for minute in range(count):
        price = max(1.0, price + rng.uniform(-1.2, 1.5))
        exchange_time = start + timedelta(minutes=minute)
        ticks.append(
            Tick(symbol, exchange_time, exchange_time + timedelta(milliseconds=50), round(price, 2), rng.randint(100, 2000))
        )
    return ticks


def aggregate_minutes(ticks: Iterable[Tick], timeframe: int = 1) -> list[Candle]:
    """Aggregate ticks into fixed minute candles; stale/out-of-order ticks are sorted."""
    if timeframe <= 0:
        raise ValueError("timeframe must be positive")
    groups: dict[tuple[str, datetime], list[Tick]] = {}
    for tick in sorted(ticks, key=lambda item: item.exchange_time):
        minute = tick.exchange_time.replace(second=0, microsecond=0)
        bucket_minute = minute.minute - minute.minute % timeframe
        bucket = minute.replace(minute=bucket_minute)
        groups.setdefault((tick.symbol, bucket), []).append(tick)
    return [
        Candle(symbol, start, group[0].price, max(t.price for t in group), min(t.price for t in group), group[-1].price, sum(t.volume for t in group))
        for (symbol, start), group in sorted(groups.items(), key=lambda item: item[0][1])
    ]


def sma(values: list[float], period: int) -> list[float | None]:
    """Calculate SMA, returning None until sufficient history exists."""
    if period <= 0:
        raise ValueError("period must be positive")
    return [None if index + 1 < period else mean(values[index + 1 - period : index + 1]) for index in range(len(values))]


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    """Calculate a simple RSI without using future values."""
    result: list[float | None] = [None] * len(values)
    for index in range(period, len(values)):
        changes = [values[pos] - values[pos - 1] for pos in range(index - period + 1, index + 1)]
        gains = [max(change, 0) for change in changes]
        losses = [-min(change, 0) for change in changes]
        average_loss = mean(losses)
        result[index] = 100.0 if average_loss == 0 else 100 - 100 / (1 + mean(gains) / average_loss)
    return result


def scan(candles: list[Candle]) -> dict[str, object]:
    """Return an explainable offline signal based on SMA, RSI and relative volume."""
    if len(candles) < 20:
        return {"status": "insufficient_data", "reasons": []}
    closes = [candle.close for candle in candles]
    volumes = [candle.volume for candle in candles]
    fast, slow = sma(closes, 5)[-1], sma(closes, 20)[-1]
    current_rsi = rsi(closes)[-1]
    relative_volume = volumes[-1] / mean(volumes[-20:-1])
    reasons: list[str] = []
    score = 50
    if fast is not None and slow is not None and fast > slow:
        score += 20
        reasons.append("5-period SMA is above 20-period SMA")
    if relative_volume >= 1.5:
        score += 15
        reasons.append(f"relative volume is {relative_volume:.2f}x")
    if current_rsi is not None and 50 <= current_rsi <= 70:
        score += 10
        reasons.append(f"RSI is {current_rsi:.1f}")
    if current_rsi is not None and current_rsi >= 75:
        score -= 15
        reasons.append(f"risk: RSI is elevated at {current_rsi:.1f}")
    return {"status": "ok", "score": max(0, min(100, score)), "reasons": reasons, "rsi": current_rsi, "relative_volume": relative_volume}


def persist(database: Path, candles: list[Candle], signal: dict[str, object]) -> None:
    """Persist offline candles and one explainable signal in SQLite."""
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL, start_time TEXT NOT NULL, open REAL, high REAL,
                low REAL, close REAL, volume INTEGER, PRIMARY KEY(symbol, start_time)
            );
            CREATE TABLE IF NOT EXISTS signals (
                symbol TEXT NOT NULL, signal_time TEXT NOT NULL, score INTEGER,
                reasons TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(c.symbol, c.start_time.isoformat(), c.open, c.high, c.low, c.close, c.volume) for c in candles],
        )
        connection.execute(
            "INSERT INTO signals VALUES (?, ?, ?, ?)",
            (candles[-1].symbol, candles[-1].start_time.isoformat(), signal.get("score"), " | ".join(signal["reasons"])),
        )


def main() -> None:
    start = datetime(2026, 7, 22, 9, 0, tzinfo=TAIPEI)
    candles = aggregate_minutes(generate_mock_ticks("2330", start, 30))
    signal = scan(candles)
    database = Path("offline_prototype.sqlite")
    persist(database, candles, signal)
    print(f"Offline run completed: {len(candles)} candles, signal={signal}, database={database.resolve()}")


if __name__ == "__main__":
    main()
