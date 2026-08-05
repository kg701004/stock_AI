"""Explainable technical signals used as a weighted confirmation, never a forecast."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from technical_layers import Bar, calculate as calculate_layers


@dataclass(frozen=True, slots=True)
class TechnicalSignal:
    score: float
    rsi14: float | None
    macd_histogram: float | None
    breakout_confirmed: bool
    status: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


def _ema(values: list[float], period: int) -> float:
    multiplier = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * multiplier + result * (1 - multiplier)
    return result


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    changes = [current - previous for previous, current in zip(closes[-period - 1:], closes[-period:])]
    gains = mean([max(change, 0) for change in changes])
    losses = mean([max(-change, 0) for change in changes])
    if losses == 0:
        return 100.0
    return round(100 - 100 / (1 + gains / losses), 2)


def _ema_series(values: list[float], period: int) -> list[float]:
    """ema_series[i] == _ema(values[:i + 1], period): cumulative EMA seeded
    from values[0], computed once in O(n) instead of recomputed from a fresh
    prefix slice at every index (which made _macd_histogram O(n^2) per call,
    and O(n^3) across technical_validation.validate()'s O(n) walk-forward
    loop -- confirmed hanging the "技術面回測驗證" screen for minutes on a
    stock with 2000+ real backfilled bars)."""
    multiplier = 2 / (period + 1)
    series = [values[0]]
    for value in values[1:]:
        series.append(value * multiplier + series[-1] * (1 - multiplier))
    return series


def _macd_histogram(closes: list[float]) -> float | None:
    if len(closes) < 35:
        return None
    ema12_series, ema26_series = _ema_series(closes, 12), _ema_series(closes, 26)
    macd_series = [ema12_series[index - 1] - ema26_series[index - 1] for index in range(26, len(closes) + 1)]
    if len(macd_series) < 9:
        return None
    return round(macd_series[-1] - _ema(macd_series, 9), 4)


def calculate(bars: list[Bar]) -> TechnicalSignal:
    """Score trend, momentum and confirmed breakouts from local daily bars.

    A score is deliberately capped to 0..100 and includes explicit warnings
    whenever a required history window is not available.
    """
    if len(bars) < 21:
        raise ValueError("technical signal needs at least 21 daily bars")
    layers = calculate_layers(bars)
    closes = [bar.close for bar in bars]
    rsi14, histogram = _rsi(closes), _macd_histogram(closes)
    previous_resistance = max(bar.high for bar in bars[-21:-1])
    breakout = closes[-1] > previous_resistance and layers.relative_volume >= 1.2
    score, reasons, warnings = 50.0, [], []
    if layers.ma60 is None:
        warnings.append("資料少於 60 日，長期趨勢只作低信心參考。")
    elif closes[-1] >= layers.ma20 >= layers.ma60:
        score += 20; reasons.append("收盤價、20 日與 60 日均線呈多頭排列。")
    elif closes[-1] < layers.ma20 < layers.ma60:
        score -= 20; reasons.append("收盤價、20 日與 60 日均線呈空頭排列。")
    if rsi14 is None:
        warnings.append("資料不足，未計算 RSI。")
    elif 45 <= rsi14 <= 70:
        score += 10; reasons.append(f"RSI {rsi14:.1f} 位於健康動能區。")
    elif rsi14 >= 80:
        score -= 12; reasons.append(f"RSI {rsi14:.1f} 過熱，禁止把追價當成趨勢確認。")
    elif rsi14 <= 25:
        score -= 6; reasons.append(f"RSI {rsi14:.1f} 極弱，需等待反轉確認。")
    if histogram is None:
        warnings.append("資料不足，未計算 MACD。")
    elif histogram > 0:
        score += 8; reasons.append("MACD 柱體為正，短期動能偏強。")
    else:
        score -= 8; reasons.append("MACD 柱體為負，短期動能偏弱。")
    if breakout:
        score += 12; reasons.append(f"突破前 20 日壓力且相對量能 {layers.relative_volume:.2f} 倍。")
    elif closes[-1] > previous_resistance:
        reasons.append("價格突破但量能未確認，不提高技術分數。")
    # Extreme momentum may be a continuation, but it is not permission to
    # promote a new entry signal without a separate pullback rule.
    if rsi14 is not None and rsi14 >= 80:
        score = min(score, 60)
    score = round(max(0, min(100, score)), 2)
    status = "技術偏多" if score >= 65 else "技術偏弱" if score < 40 else "技術中性"
    return TechnicalSignal(score, rsi14, histogram, breakout, status, tuple(reasons), tuple(warnings))
