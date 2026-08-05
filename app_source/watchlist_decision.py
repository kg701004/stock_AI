"""Explainable target, stop and reference-price decisions for a watchlist item."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class WatchDecision:
    target_price: float; stop_price: float; action: str; reason: str

def evaluate(reference_price: float, market_price: float, target_price: float, stop_price: float) -> tuple[str, str]:
    """Evaluate against the last analysis levels; levels never move on price refresh."""
    if reference_price <= 0 or market_price <= 0: raise ValueError("invalid decision inputs")
    if market_price >= target_price: action = "停利"
    elif market_price <= stop_price: action = "停損"
    elif market_price >= reference_price: action = "續抱／觀察"
    else: action = "觀察風險"
    return action, f"現價 {market_price:.2f}；已鎖定目標 {target_price:.2f}、停損 {stop_price:.2f}；相對參考價 {(market_price / reference_price - 1):+.1%}。"

def calculate(reference_price: float, market_price: float, score: float, risk_score: float) -> WatchDecision:
    """Use current market conditions, never the reference price, for target/stop levels."""
    if reference_price <= 0 or market_price <= 0 or not 0 <= score <= 100 or not 0 <= risk_score <= 100: raise ValueError("invalid decision inputs")
    upside = min(0.22, max(0.04, 0.05 + score * 0.0015 - risk_score * 0.00035))
    downside = min(0.15, max(0.03, 0.11 - score * 0.00035 + risk_score * 0.00045))
    target, stop = round(market_price * (1 + upside), 2), round(market_price * (1 - downside), 2)
    action, base_reason = evaluate(reference_price, market_price, target, stop)
    return WatchDecision(target, stop, action, f"分數 {score:.1f}、風險 {risk_score:.1f}；分析時目標 +{upside:.1%}、停損 -{downside:.1%}。{base_reason}")
