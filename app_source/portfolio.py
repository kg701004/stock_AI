"""Multi-owner holdings, profit/loss calculation and explainable position advice."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from database_utils import database_connection
from weighted_analysis import Assessment


@dataclass(frozen=True, slots=True)
class Position:
    owner: str
    symbol: str
    shares: int
    average_cost: float
    current_price: float
    as_of: datetime

    def __post_init__(self) -> None:
        if not self.owner.strip():
            raise ValueError("owner cannot be blank")
        if not self.symbol.isdigit() or len(self.symbol) != 4:
            raise ValueError("symbol must be a four-digit Taiwan stock code")
        if self.shares <= 0 or self.average_cost <= 0 or self.current_price <= 0:
            raise ValueError("shares, average_cost and current_price must be positive")


@dataclass(frozen=True, slots=True)
class PositionMetrics:
    market_value: float
    cost_value: float
    unrealized_profit: float
    unrealized_profit_pct: float


@dataclass(frozen=True, slots=True)
class PositionAdvice:
    owner: str
    symbol: str
    action: str
    metrics: PositionMetrics
    assessment_score: float
    assessment_classification: str
    reasons: tuple[str, ...]
    triggered_conditions: tuple[str, ...]


def load_position_rules(path: Path) -> Mapping[str, float | str]:
    """Load user-adjustable position decision thresholds from JSON."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {"version", "add_minimum_score", "add_maximum_risk_score", "hold_minimum_score", "reduce_maximum_score", "reduce_minimum_risk_score", "maximum_position_weight_pct"}
    if not required <= set(payload):
        raise ValueError("position rules are incomplete")
    for name in required - {"version"}:
        if not isinstance(payload[name], (int, float)) or not 0 <= payload[name] <= 100:
            raise ValueError(f"{name} must be from 0 to 100")
    if not payload["add_minimum_score"] > payload["hold_minimum_score"] > payload["reduce_maximum_score"]:
        raise ValueError("add, hold and reduce score thresholds must descend")
    return payload


def load_positions_csv(path: Path) -> list[Position]:
    """Load multi-owner positions from UTF-8 CSV."""
    required = {"owner", "symbol", "shares", "average_cost", "current_price", "as_of"}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            raise ValueError(f"positions CSV is missing: {sorted(required - set(reader.fieldnames or []))}")
        positions: list[Position] = []
        for row_number, row in enumerate(reader, start=2):
            try:
                positions.append(Position(row["owner"], row["symbol"], int(row["shares"]), float(row["average_cost"]), float(row["current_price"]), datetime.fromisoformat(row["as_of"])))
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid position row {row_number}: {error}") from error
    return positions


def calculate_metrics(position: Position) -> PositionMetrics:
    """Calculate unrealized P/L before transaction taxes and commissions."""
    cost_value = round(position.shares * position.average_cost, 2)
    market_value = round(position.shares * position.current_price, 2)
    profit = round(market_value - cost_value, 2)
    return PositionMetrics(market_value, cost_value, profit, round(profit / cost_value * 100, 2))


def advise_position(position: Position, assessment: Assessment, risk_score: float, rules: Mapping[str, float | str]) -> PositionAdvice:
    """Recommend add/hold/reduce from configurable rules, never an order command."""
    metrics = calculate_metrics(position)
    triggered: list[str] = []
    reasons: list[str] = []
    if assessment.final_score >= float(rules["add_minimum_score"]) and risk_score <= float(rules["add_maximum_risk_score"]):
        action = "加碼觀察"
        triggered.extend([f"總分 {assessment.final_score} ≥ 加碼門檻 {rules['add_minimum_score']}", f"風險分數 {risk_score} ≤ 加碼風險上限 {rules['add_maximum_risk_score']}"])
        reasons.append("趨勢與綜合評分強，且風險未超出設定上限；仍須確認部位集中度與可交易性。")
    elif assessment.final_score <= float(rules["reduce_maximum_score"]) or risk_score >= float(rules["reduce_minimum_risk_score"]):
        action = "減碼／風險控管"
        if assessment.final_score <= float(rules["reduce_maximum_score"]):
            triggered.append(f"總分 {assessment.final_score} ≤ 減碼門檻 {rules['reduce_maximum_score']}")
        if risk_score >= float(rules["reduce_minimum_risk_score"]):
            triggered.append(f"風險分數 {risk_score} ≥ 減碼風險門檻 {rules['reduce_minimum_risk_score']}")
        reasons.append("綜合條件轉弱或風險升高；應重新確認失效條件與可承受回撤。")
    else:
        action = "續抱觀察"
        triggered.append(f"總分介於續抱與加減碼門檻之間（{assessment.final_score}）")
        reasons.append("目前未觸發加碼或減碼規則，維持觀察並等待下一次資料更新。")
    reasons.extend(item.note for item in assessment.contributions if item.note != "No explanatory note supplied.")
    reasons.extend(assessment.warnings)
    return PositionAdvice(position.owner, position.symbol, action, metrics, assessment.final_score, assessment.classification, tuple(reasons), tuple(triggered))


def persist_position_advice(database: Path, advice: PositionAdvice, as_of: datetime, rules_version: str) -> None:
    """Save an owner-specific advice record for later review and performance analysis."""
    with database_connection(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS position_advice (
                id INTEGER PRIMARY KEY, owner TEXT NOT NULL, symbol TEXT NOT NULL, as_of TEXT NOT NULL,
                action TEXT NOT NULL, market_value REAL NOT NULL, cost_value REAL NOT NULL,
                profit REAL NOT NULL, profit_pct REAL NOT NULL, assessment_score REAL NOT NULL,
                classification TEXT NOT NULL, rules_version TEXT NOT NULL,
                reasons_json TEXT NOT NULL, triggers_json TEXT NOT NULL
            )
        """)
        connection.execute(
            "INSERT INTO position_advice(owner, symbol, as_of, action, market_value, cost_value, profit, profit_pct, assessment_score, classification, rules_version, reasons_json, triggers_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (advice.owner, advice.symbol, as_of.isoformat(), advice.action, advice.metrics.market_value, advice.metrics.cost_value, advice.metrics.unrealized_profit, advice.metrics.unrealized_profit_pct, advice.assessment_score, advice.assessment_classification, rules_version, json.dumps(advice.reasons, ensure_ascii=False), json.dumps(advice.triggered_conditions, ensure_ascii=False)),
        )


def advice_as_dict(advice: PositionAdvice) -> dict[str, object]:
    """Return a JSON-friendly representation for a future GUI table/detail view."""
    return asdict(advice)
