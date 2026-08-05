"""Chronological, out-of-sample validation for technical signals."""

from __future__ import annotations

from dataclasses import dataclass

from technical_layers import Bar
from technical_signal import calculate


@dataclass(frozen=True, slots=True)
class ValidationResult:
    sample: str
    signals: int
    hit_rate_pct: float
    average_return_pct: float


def validate(bars: list[Bar], threshold: float = 65, holding_days: int = 5, train_fraction: float = 0.7) -> tuple[ValidationResult, ValidationResult]:
    """Compare train and later untouched samples without future-data leakage."""
    if not 0 < train_fraction < 1 or holding_days < 1:
        raise ValueError("invalid validation settings")
    if len(bars) < 21 + holding_days + 2:
        raise ValueError("not enough bars for validation")
    split = int(len(bars) * train_fraction)
    results: list[list[float]] = [[], []]
    for current in range(20, len(bars) - holding_days):
        signal = calculate(bars[:current + 1])
        if signal.score >= threshold:
            results[0 if current < split else 1].append((bars[current + holding_days].close / bars[current].close - 1) * 100)
    def report(name: str, values: list[float]) -> ValidationResult:
        return ValidationResult(name, len(values), round(sum(value > 0 for value in values) / len(values) * 100, 2) if values else 0, round(sum(values) / len(values), 2) if values else 0)
    return report("樣本內", results[0]), report("樣本外", results[1])
