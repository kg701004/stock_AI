"""Configurable, explainable scoring for end-of-day stock decisions.

No market-data provider is embedded here.  Connectors or CSV imports submit
normalized 0--100 factor scores, while this module makes every weighting choice
visible, validated, reproducible and persistable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from database_utils import database_connection

FACTOR_NAMES = (
    "technical", "market_breadth", "sector_rotation", "fundamentals",
    "institutional_flow", "derivatives", "global_risk", "sentiment", "events",
    "liquidity", "valuation",
)


@dataclass(frozen=True, slots=True)
class WeightConfig:
    version: str
    weights: Mapping[str, float]
    thresholds: Mapping[str, float]
    digest: str
    weight_source: str


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    """One stock snapshot with each benefit factor expressed from 0 to 100.

    ``risk_score`` is separate because it subtracts from the composite score.
    It is deliberately not a weighted benefit factor.
    """

    symbol: str
    as_of: datetime
    factors: Mapping[str, float]
    risk_score: float
    notes: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.symbol.isdigit() or len(self.symbol) != 4:
            raise ValueError("symbol must be a four-digit Taiwan stock code")
        unknown = set(self.factors) - set(FACTOR_NAMES)
        if unknown:
            raise ValueError(f"unknown factors: {sorted(unknown)}")
        for factor, score in self.factors.items():
            _validate_score(factor, score)
        _validate_score("risk_score", self.risk_score)


@dataclass(frozen=True, slots=True)
class FactorContribution:
    factor: str
    raw_score: float
    normalized_weight: float
    contribution: float
    note: str


@dataclass(frozen=True, slots=True)
class Assessment:
    symbol: str
    as_of: datetime
    config_version: str
    config_digest: str
    weight_source: str
    benefit_score: float
    risk_penalty: float
    final_score: float
    classification: str
    contributions: tuple[FactorContribution, ...]
    warnings: tuple[str, ...]


def _validate_score(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or not 0 <= value <= 100:
        raise ValueError(f"{name} must be a number from 0 to 100")


def load_weight_config(path: Path) -> WeightConfig:
    """Load and validate user-editable JSON settings, normalizing weights later."""
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    weights = payload.get("weights")
    weight_source = "analysis_weights"
    judgement_path = path.with_name("judgement_weights.json")
    if judgement_path.exists() and path.name == "analysis_weights.json":
        from judgement_weights import load as load_judgement_weights
        weights = load_judgement_weights(judgement_path)
        weight_source = "judgement_weights"
    thresholds = payload.get("thresholds")
    if not isinstance(weights, dict) or set(weights) != set(FACTOR_NAMES):
        missing = set(FACTOR_NAMES) - set(weights or {})
        extra = set(weights or {}) - set(FACTOR_NAMES)
        raise ValueError(f"weights must contain exactly the known factors; missing={sorted(missing)}, extra={sorted(extra)}")
    if not isinstance(thresholds, dict):
        raise ValueError("thresholds must be an object")
    for name, value in weights.items():
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"weight {name} must be non-negative")
    if sum(weights.values()) <= 0:
        raise ValueError("at least one weight must be positive")
    required_thresholds = {"strong_watch", "bullish_watch", "neutral", "high_risk", "minimum_liquidity_score", "maximum_risk_penalty"}
    if not required_thresholds <= set(thresholds):
        raise ValueError("thresholds are incomplete")
    for name, value in thresholds.items():
        if not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError(f"threshold {name} must be from 0 to 100")
    if not thresholds["strong_watch"] >= thresholds["bullish_watch"] >= thresholds["neutral"] >= thresholds["high_risk"]:
        raise ValueError("classification thresholds must descend from strong_watch to high_risk")
    # Digest reflects the weights/thresholds actually used to score, not the
    # analysis_weights.json file bytes -- judgement_weights.json can silently
    # replace the weights above, and the digest must follow that substitution.
    resolved = json.dumps({"weights": weights, "thresholds": thresholds}, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(resolved).hexdigest()
    return WeightConfig(str(payload.get("version", "unversioned")), weights, thresholds, digest, weight_source)


def assess_stock(data: AnalysisInput, config: WeightConfig) -> Assessment:
    """Calculate a transparent score and apply data-quality/risk guardrails."""
    total_weight = sum(config.weights.values())
    contributions = tuple(
        FactorContribution(
            factor,
            round(float(data.factors.get(factor, 50.0)), 2),
            round(config.weights[factor] / total_weight, 6),
            round(float(data.factors.get(factor, 50.0)) * config.weights[factor] / total_weight, 2),
            data.notes.get(factor, "No explanatory note supplied."),
        )
        for factor in FACTOR_NAMES
    )
    # Keep classification mathematically stable at thresholds; contributions
    # remain rounded only for display.
    benefit_score = round(sum(float(data.factors.get(factor, 50.0)) * config.weights[factor] / total_weight for factor in FACTOR_NAMES), 2)
    risk_penalty = round(data.risk_score * config.thresholds["maximum_risk_penalty"] / 100, 2)
    final_score = round(max(0.0, benefit_score - risk_penalty), 2)
    warnings: list[str] = []
    if data.factors.get("liquidity", 50.0) < config.thresholds["minimum_liquidity_score"]:
        warnings.append("Liquidity is below the configured minimum; do not assume a backtest fill is executable.")
    if data.risk_score >= 75:
        warnings.append("Risk score is elevated; treat this as observation only.")
    if data.as_of.tzinfo is None:
        warnings.append("Timestamp has no timezone; do not use this record in time-sensitive backtests.")
    thresholds = config.thresholds
    classification = (
        "strong_watch" if final_score >= thresholds["strong_watch"] else
        "bullish_watch" if final_score >= thresholds["bullish_watch"] else
        "neutral" if final_score >= thresholds["neutral"] else
        "high_risk" if final_score < thresholds["high_risk"] else "weak"
    )
    return Assessment(data.symbol, data.as_of, config.version, config.digest, config.weight_source, benefit_score, risk_penalty, final_score, classification, contributions, tuple(warnings))


def persist_assessment(database: Path, assessment: Assessment) -> None:
    """Store a reproducible assessment and its complete contribution breakdown."""
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS assessments (
                id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, as_of TEXT NOT NULL,
                config_version TEXT NOT NULL, config_digest TEXT NOT NULL, weight_source TEXT NOT NULL DEFAULT '',
                benefit_score REAL NOT NULL, risk_penalty REAL NOT NULL,
                final_score REAL NOT NULL, classification TEXT NOT NULL, warnings_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assessment_contributions (
                assessment_id INTEGER NOT NULL, factor TEXT NOT NULL, raw_score REAL NOT NULL,
                normalized_weight REAL NOT NULL, contribution REAL NOT NULL, note TEXT NOT NULL,
                FOREIGN KEY(assessment_id) REFERENCES assessments(id)
            );
        """)
        # A database created before weight_source existed already has an
        # assessments table, so CREATE TABLE IF NOT EXISTS above is a no-op
        # for it -- migrate it explicitly instead of failing on INSERT.
        existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(assessments)")}
        if "weight_source" not in existing_columns:
            connection.execute("ALTER TABLE assessments ADD COLUMN weight_source TEXT NOT NULL DEFAULT ''")
        cursor = connection.execute(
            "INSERT INTO assessments(symbol, as_of, config_version, config_digest, weight_source, benefit_score, risk_penalty, final_score, classification, warnings_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (assessment.symbol, assessment.as_of.isoformat(), assessment.config_version, assessment.config_digest, assessment.weight_source, assessment.benefit_score, assessment.risk_penalty, assessment.final_score, assessment.classification, json.dumps(assessment.warnings)),
        )
        contribution_cursor = connection.executemany(
            "INSERT INTO assessment_contributions VALUES (?, ?, ?, ?, ?, ?)",
            [(cursor.lastrowid, item.factor, item.raw_score, item.normalized_weight, item.contribution, item.note) for item in assessment.contributions],
        )
        contribution_cursor.close()
        cursor.close()


def assessment_as_dict(assessment: Assessment) -> dict[str, object]:
    """Return a JSON-serializable representation for reporting or UI use."""
    return asdict(assessment)
