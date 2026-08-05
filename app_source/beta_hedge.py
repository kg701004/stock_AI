"""Beta-hedge futures contract-count suggestions -- advisory only, never an order.

Uses the target-beta formula (see 功能檢測與改善計畫.md section 7, method 3):
setting target_beta=0 reduces to the plain static-beta full hedge (method 1),
so one formula covers both without maintaining two code paths.
"""
from __future__ import annotations

from dataclasses import dataclass

CONTRACT_POINT_VALUES = {"TX": 200.0, "MTX": 50.0, "TMF": 10.0}
CONTRACT_LABELS = {"TX": "大台 TX", "MTX": "小台 MTX", "TMF": "微台 TMF"}


@dataclass(frozen=True, slots=True)
class HedgeSuggestion:
    contracts: float
    direction: str
    formula: str
    target_contracts: float = 0.0
    held_contracts: float = 0.0


def suggest_hedge(portfolio_value: float, current_beta: float, target_beta: float, index_points: float, contract: str, held_contracts: float = 0.0) -> HedgeSuggestion:
    """Suggest how many index-futures contracts to trade to move from current_beta to target_beta.

    ``held_contracts`` (signed: negative = short, positive = long) is exposure the
    owner already holds from a previous hedge action -- the suggestion becomes the
    remaining trade needed to reach the target, not a fresh full hedge every time.
    """
    if portfolio_value <= 0:
        raise ValueError("portfolio_value must be positive")
    if index_points <= 0:
        raise ValueError("index_points must be positive")
    if contract not in CONTRACT_POINT_VALUES:
        raise ValueError(f"unknown contract: {contract!r}; expected one of {sorted(CONTRACT_POINT_VALUES)}")
    point_value = CONTRACT_POINT_VALUES[contract]
    notional_per_contract = index_points * point_value
    target_contracts = (target_beta - current_beta) * portfolio_value / notional_per_contract
    adjustment = target_contracts - held_contracts
    if adjustment < -1e-9:
        direction = "放空"
    elif adjustment > 1e-9:
        direction = "做多"
    else:
        direction = "不需操作"
    formula = (
        f"目標總口數 ({portfolio_value:,.0f} × ({target_beta:.2f} − {current_beta:.2f})) "
        f"÷ ({index_points:,.0f} × {point_value:.0f}) ≈ {target_contracts:.2f} 口；"
        f"已持有 {held_contracts:.2f} 口，尚需交易 {adjustment:.2f} 口"
    ) if held_contracts else (
        f"({portfolio_value:,.0f} × ({target_beta:.2f} − {current_beta:.2f})) "
        f"÷ ({index_points:,.0f} × {point_value:.0f}) ≈ {target_contracts:.2f} 口"
    )
    return HedgeSuggestion(round(abs(adjustment), 2), direction, formula, round(target_contracts, 2), held_contracts)
