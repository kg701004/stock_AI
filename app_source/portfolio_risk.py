"""Portfolio-level exposure and correlation controls for end-of-day research."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from portfolio import Position, calculate_metrics


@dataclass(frozen=True, slots=True)
class SecurityMetadata:
    symbol: str
    sector: str
    beta: float


@dataclass(frozen=True, slots=True)
class PortfolioRiskAssessment:
    owner: str
    total_market_value: float
    portfolio_beta: float
    holding_weights_pct: Mapping[str, float]
    sector_weights_pct: Mapping[str, float]
    high_correlation_pairs: tuple[tuple[str, str, float], ...]
    warnings: tuple[str, ...]


def load_risk_rules(path: Path) -> Mapping[str, float | str]:
    """Load user-adjustable concentration and correlation thresholds."""
    rules = json.loads(path.read_text(encoding="utf-8"))
    required = {"version", "maximum_position_weight_pct", "maximum_sector_weight_pct", "maximum_portfolio_beta", "high_correlation_threshold"}
    if not required <= set(rules):
        raise ValueError("portfolio risk rules are incomplete")
    if not 0 < rules["maximum_position_weight_pct"] <= 100 or not 0 < rules["maximum_sector_weight_pct"] <= 100:
        raise ValueError("position and sector limits must be between 0 and 100")
    if rules["maximum_portfolio_beta"] <= 0 or not -1 <= rules["high_correlation_threshold"] <= 1:
        raise ValueError("beta/correlation thresholds are invalid")
    return rules


def stress_correlation(correlation: float, shock_factor: float) -> float:
    """Model crisis-driven correlation convergence: diversification empirically
    shrinks in market stress as correlations get pulled toward 1 (e.g. 2008,
    2020) regardless of their calm-market sign. This is a scenario assumption
    applied to a real measured correlation, not a historical measurement or a
    forecast -- callers must label output accordingly.
    """
    if not -1 <= correlation <= 1:
        raise ValueError("correlation must be between -1 and 1")
    if not 0 <= shock_factor <= 1:
        raise ValueError("shock_factor must be between 0 and 1")
    return round(correlation + (1 - correlation) * shock_factor, 4)


def pearson_correlation(left: list[float], right: list[float]) -> float:
    """Calculate correlation for aligned return series without third-party libraries."""
    if len(left) != len(right) or len(left) < 3:
        raise ValueError("correlation needs at least three aligned observations")
    left_mean, right_mean = sum(left) / len(left), sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_scale == 0 or right_scale == 0:
        raise ValueError("correlation is undefined for a constant series")
    return round(numerator / (left_scale * right_scale), 4)


def assess_owner_portfolio(owner: str, positions: list[Position], metadata: Mapping[str, SecurityMetadata], rules: Mapping[str, float | str], return_series: Mapping[str, list[float]] | None = None) -> PortfolioRiskAssessment:
    """Assess one owner's concentration, beta, sector and correlation exposure."""
    owner_positions = [position for position in positions if position.owner == owner]
    if not owner_positions:
        raise ValueError(f"no positions found for {owner}")
    market_values = {position.symbol: calculate_metrics(position).market_value for position in owner_positions}
    total = sum(market_values.values())
    holding_weights = {symbol: round(value / total * 100, 2) for symbol, value in market_values.items()}
    sector_values: dict[str, float] = defaultdict(float)
    weighted_beta, warnings = 0.0, []
    for symbol, value in market_values.items():
        info = metadata.get(symbol)
        if info is None:
            warnings.append(f"{symbol} 缺少產業與 Beta 資料，無法完整評估曝險；Beta 以市場平均值 1.0 估算。")
            sector_values["未分類"] += value
            # A missing beta must NOT be treated as 0 (zero market correlation) --
            # that silently understates portfolio_beta, which feeds directly into
            # beta_hedge.suggest_hedge()'s futures-contract sizing and would cause
            # an under-hedged "hedged" portfolio. Default to the market-average
            # beta of 1.0 instead, matching portfolio_advanced_risk.py's stress
            # test so the two risk screens agree on how to handle missing data.
            weighted_beta += value / total * 1.0
            continue
        sector_values[info.sector] += value
        weighted_beta += value / total * info.beta
    sector_weights = {sector: round(value / total * 100, 2) for sector, value in sector_values.items()}
    for symbol, weight in holding_weights.items():
        if weight > float(rules["maximum_position_weight_pct"]):
            warnings.append(f"{symbol} 佔組合 {weight:.2f}%，超過單一持股上限 {rules['maximum_position_weight_pct']}%。")
    for sector, weight in sector_weights.items():
        if weight > float(rules["maximum_sector_weight_pct"]):
            warnings.append(f"{sector} 佔組合 {weight:.2f}%，超過產業上限 {rules['maximum_sector_weight_pct']}%。")
    if weighted_beta > float(rules["maximum_portfolio_beta"]):
        warnings.append(f"組合加權 Beta {weighted_beta:.2f}，超過上限 {rules['maximum_portfolio_beta']}。")
    pairs: list[tuple[str, str, float]] = []
    if return_series:
        symbols = [symbol for symbol in market_values if symbol in return_series]
        for index, left in enumerate(symbols):
            for right in symbols[index + 1:]:
                try:
                    correlation = pearson_correlation(return_series[left], return_series[right])
                except ValueError:
                    continue
                if correlation >= float(rules["high_correlation_threshold"]):
                    pairs.append((left, right, correlation))
                    warnings.append(f"{left} 與 {right} 的報酬相關性 {correlation:.2f} 偏高，分散效果有限。")
    return PortfolioRiskAssessment(owner, round(total, 2), round(weighted_beta, 3), holding_weights, sector_weights, tuple(pairs), tuple(warnings))
