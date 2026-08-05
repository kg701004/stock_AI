"""Rule-based, configurable diversification and target-allocation suggestions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from portfolio_risk import SecurityMetadata, load_risk_rules
from transaction_ledger import LedgerHolding


@dataclass(frozen=True, slots=True)
class AllocationSuggestion:
    symbol: str
    sector: str
    score: float
    current_weight_pct: float
    target_weight_pct: float
    adjustment_value: float
    action: str
    reason: str


@dataclass(frozen=True, slots=True)
class AllocationPlan:
    owner: str
    portfolio_value: float  # total net worth = invested market value + cash_balance
    cash_balance: float
    cash_weight_pct: float
    suggestions: tuple[AllocationSuggestion, ...]
    warnings: tuple[str, ...]


def load_allocation_rules(path: Path) -> Mapping[str, float | str]:
    """Load allocation constraints adjustable by the user.

    Position/sector concentration caps are deliberately NOT duplicated in
    this file -- they are read from portfolio_risk_rules.json (the single
    shared source), so this module and portfolio_risk.py can never disagree
    about how concentrated a holding is allowed to be.
    """
    rules = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "version", "target_holding_count",
        "minimum_candidate_score", "rebalance_tolerance_pct",
    }
    if not required <= set(rules):
        raise ValueError("allocation rules are incomplete")
    for name in required - {"version", "target_holding_count"}:
        if not 0 <= float(rules[name]) <= 100:
            raise ValueError(f"{name} must be from 0 to 100")
    if int(rules["target_holding_count"]) < 1:
        raise ValueError("target_holding_count must be at least one")
    risk_rules = load_risk_rules(path.with_name("portfolio_risk_rules.json"))
    return {
        **rules,
        "maximum_position_weight_pct": risk_rules["maximum_position_weight_pct"],
        "maximum_sector_weight_pct": risk_rules["maximum_sector_weight_pct"],
    }


def build_allocation_plan(
    owner: str,
    holdings: list[LedgerHolding],
    scores: Mapping[str, float],
    metadata: Mapping[str, SecurityMetadata],
    rules: Mapping[str, float | str],
    candidate_symbols: Iterable[str] = (),
    cash_balance: float = 0.0,
) -> AllocationPlan:
    """Build a capped, score-ranked allocation across holdings, candidates, and cash.

    ``candidate_symbols`` normally comes from the user's watchlist and imported
    factor-score file.  A candidate is never treated as owned: its current
    weight is zero and the output is a research suggestion only.

    ``cash_balance`` makes every weight a share of TOTAL net worth (invested +
    cash), not just invested value -- and ``minimum_cash_reserve_pct`` in
    ``rules`` (previously accepted but silently ignored) now actually caps how
    much of net worth gets targeted at positions, reserving the rest as cash.
    """
    if cash_balance < 0:
        raise ValueError("cash_balance cannot be negative")
    owned_by_symbol = {
        item.symbol: item for item in holdings
        if item.owner == owner and item.market_value is not None and item.shares > 0
    }
    invested = sum(item.market_value or 0 for item in owned_by_symbol.values())
    portfolio_value = invested + cash_balance
    if portfolio_value <= 0:
        raise ValueError("portfolio needs a positive invested market value or cash balance")

    min_score = float(rules["minimum_candidate_score"])
    all_symbols = set(owned_by_symbol) | set(candidate_symbols)
    eligible = sorted(
        (symbol for symbol in all_symbols if scores.get(symbol, 0) >= min_score),
        key=lambda symbol: (-scores.get(symbol, 0), symbol),
    )
    warnings: list[str] = []
    if not eligible:
        warnings.append("沒有達到最低分數的候選標的；不建立新的目標配置。")
    target_count = int(rules["target_holding_count"])
    selected = eligible[:target_count]
    if len(selected) < target_count:
        warnings.append(f"合格候選僅 {len(selected)} 檔，低於目標持股數 {target_count} 檔；未以低分標的硬湊。")

    reserve_pct = float(rules.get("minimum_cash_reserve_pct", 0))
    investable_weight = max(0.0, 100.0 - reserve_pct)
    # scores.get(...) here matches the eligibility check above (scores.get(symbol, 0)
    # >= min_score) -- a symbol can qualify via the default 0 when min_score <= 0
    # without ever having a real entry in `scores`, and direct scores[symbol]
    # indexing would then raise KeyError.
    total_score = sum(max(scores.get(symbol, 0), 1) for symbol in selected)
    sector_used: dict[str, float] = {}
    targets: dict[str, float] = {}
    for symbol in selected:
        info = metadata.get(symbol, SecurityMetadata(symbol, "未分類", 1.0))
        raw = investable_weight * max(scores.get(symbol, 0), 1) / total_score
        sector_remaining = float(rules["maximum_sector_weight_pct"]) - sector_used.get(info.sector, 0)
        target = max(0.0, min(raw, float(rules["maximum_position_weight_pct"]), sector_remaining))
        targets[symbol] = round(target, 2)
        sector_used[info.sector] = sector_used.get(info.sector, 0.0) + target
    unused = round(investable_weight - sum(targets.values()), 2)
    if unused > 0:
        warnings.append(f"因個股／產業上限，尚有 {unused:.2f}% 保留為現金，未指派給個別持股。")

    tolerance_value = portfolio_value * float(rules["rebalance_tolerance_pct"]) / 100
    suggestions: list[AllocationSuggestion] = []
    for symbol in sorted(all_symbols):
        holding = owned_by_symbol.get(symbol)
        current_value = 0.0 if holding is None else holding.market_value or 0.0
        current = current_value / portfolio_value * 100
        target = targets.get(symbol, 0.0)
        adjustment = portfolio_value * (target - current) / 100
        info = metadata.get(symbol, SecurityMetadata(symbol, "未分類", 1.0))
        score = scores.get(symbol, 0.0)
        if holding is None and target > 0:
            action, reason = "建立部位", "自選／評分候選符合門檻，且配置後仍符合集中度限制。"
        elif adjustment > tolerance_value:
            action, reason = "加碼", "目標權重高於目前權重，且未超過個股與產業上限。"
        elif adjustment < -tolerance_value:
            action, reason = "減碼", "目前權重高於規則目標，或評分未達最低候選門檻。"
        else:
            action, reason = "維持", "目前權重接近規則目標，未達再平衡容忍門檻。"
        suggestions.append(AllocationSuggestion(symbol, info.sector, score, round(current, 2), target, round(adjustment, 2), action, reason))

    cash_weight_pct = round(cash_balance / portfolio_value * 100, 2)
    if cash_weight_pct < reserve_pct - float(rules["rebalance_tolerance_pct"]):
        warnings.append(f"現金部位 {cash_weight_pct:.2f}%，低於設定的現金保留目標 {reserve_pct:.2f}%；可考慮減碼持股以拉高現金水位。")

    return AllocationPlan(
        owner, round(portfolio_value, 2), round(cash_balance, 2), cash_weight_pct,
        tuple(sorted(suggestions, key=lambda item: (item.action != "減碼", -abs(item.adjustment_value), item.symbol))),
        tuple(warnings),
    )
