"""Explainable market-sentiment and VIX-style fear scoring for end-of-day use."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from database_utils import database_connection


@dataclass(frozen=True, slots=True)
class ScoreExplanation:
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SentimentInputs:
    advancers: int
    decliners: int
    new_highs: int
    new_lows: int
    limit_ups: int
    limit_downs: int
    put_call_ratio: float | None = None
    institutional_flow_score: float | None = None
    official_event_sentiment_score: float | None = None


@dataclass(frozen=True, slots=True)
class FearInputs:
    vix_value: float
    vix_percentile: float
    vix_5d_change_pct: float
    taiwan_night_return_pct: float | None = None
    taiwan_put_call_ratio: float | None = None


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _optional_score(name: str, value: float | None) -> float | None:
    if value is not None and not 0 <= value <= 100:
        raise ValueError(f"{name} must be from 0 to 100")
    return value


def score_sentiment(inputs: SentimentInputs) -> ScoreExplanation:
    """Score domestic market sentiment; higher values mean more constructive sentiment."""
    if min(inputs.advancers, inputs.decliners, inputs.new_highs, inputs.new_lows, inputs.limit_ups, inputs.limit_downs) < 0:
        raise ValueError("market breadth counts cannot be negative")
    score, reasons = 50.0, []
    breadth = inputs.advancers / max(1, inputs.advancers + inputs.decliners)
    if breadth >= 0.6:
        score += 15; reasons.append("上漲家數明顯高於下跌家數")
    elif breadth <= 0.4:
        score -= 15; reasons.append("下跌家數明顯高於上漲家數")
    if inputs.new_highs > inputs.new_lows * 2:
        score += 10; reasons.append("創新高家數明顯多於創新低")
    elif inputs.new_lows > inputs.new_highs * 2:
        score -= 10; reasons.append("創新低家數明顯多於創新高")
    if inputs.limit_ups > inputs.limit_downs * 2:
        score += 8; reasons.append("漲停家數相對較多")
    elif inputs.limit_downs > inputs.limit_ups * 2:
        score -= 8; reasons.append("跌停家數相對較多")
    if inputs.put_call_ratio is not None:
        if inputs.put_call_ratio >= 1.3:
            score -= 7; reasons.append("Put/Call 比偏高，避險需求增加")
        elif inputs.put_call_ratio <= 0.8:
            score += 5; reasons.append("Put/Call 比偏低，選擇權情緒較穩定")
    for label, value in (("法人流向", _optional_score("institutional_flow_score", inputs.institutional_flow_score)), ("官方事件", _optional_score("official_event_sentiment_score", inputs.official_event_sentiment_score))):
        if value is not None:
            score += (value - 50) * 0.2
            reasons.append(f"{label}分數納入情緒調整：{value:.0f}")
    return ScoreExplanation(_clamp(score), tuple(reasons))


def score_fear(inputs: FearInputs) -> ScoreExplanation:
    """Score global risk from VIX and Taiwan overnight context; higher means lower fear."""
    if inputs.vix_value <= 0 or not 0 <= inputs.vix_percentile <= 100:
        raise ValueError("VIX value must be positive and percentile must be from 0 to 100")
    score, reasons = 50.0, [f"VIX 水位 {inputs.vix_value:.2f}，歷史百分位 {inputs.vix_percentile:.0f}"]
    if inputs.vix_percentile >= 90:
        score -= 25; reasons.append("VIX 處於歷史極端高檔")
    elif inputs.vix_percentile >= 75:
        score -= 15; reasons.append("VIX 處於歷史高檔")
    elif inputs.vix_percentile <= 25:
        score += 15; reasons.append("VIX 處於歷史低檔")
    elif inputs.vix_percentile <= 50:
        score += 5; reasons.append("VIX 未高於歷史中位數")
    if inputs.vix_5d_change_pct >= 20:
        score -= 10; reasons.append("VIX 五日快速上升")
    elif inputs.vix_5d_change_pct <= -15:
        score += 5; reasons.append("VIX 五日明顯回落")
    if inputs.taiwan_night_return_pct is not None:
        if inputs.taiwan_night_return_pct <= -1.5:
            score -= 10; reasons.append("台指夜盤顯著下跌")
        elif inputs.taiwan_night_return_pct >= 1:
            score += 5; reasons.append("台指夜盤上漲")
    if inputs.taiwan_put_call_ratio is not None and inputs.taiwan_put_call_ratio >= 1.3:
        score -= 5; reasons.append("台指選擇權 Put/Call 比偏高")
    return ScoreExplanation(_clamp(score), tuple(reasons))


def build_sentiment_factors(sentiment: SentimentInputs, fear: FearInputs) -> tuple[dict[str, float], dict[str, str]]:
    """Return factor values/notes ready for ``AnalysisInput`` or factor-score CSV."""
    sentiment_result, fear_result = score_sentiment(sentiment), score_fear(fear)
    return ({"sentiment": sentiment_result.score, "global_risk": fear_result.score}, {"sentiment": "；".join(sentiment_result.reasons), "global_risk": "；".join(fear_result.reasons)})


def global_risk_factor_score(database: Path) -> tuple[float | None, str]:
    """Calculate the global risk factor score using VIX historical percentile and 5-day change."""
    if not database.exists():
        return None, "VIX資料不足，尚無法計算全球風險因子"
    try:
        with database_connection(database) as c:
            rows = c.execute(
                "SELECT value FROM vix_history ORDER BY trading_date DESC LIMIT 90"
            ).fetchall()
    except Exception:
        return None, "VIX資料不足，尚無法計算全球風險因子"

    if len(rows) < 6:
        return None, "VIX資料不足，尚無法計算全球風險因子"

    values = [r[0] for r in rows]
    latest_vix = values[0]
    vix_5d_ago = values[5]

    if vix_5d_ago <= 0:
        vix_5d_change_pct = 0.0
    else:
        vix_5d_change_pct = ((latest_vix - vix_5d_ago) / vix_5d_ago) * 100

    less = sum(1 for v in values if v < latest_vix)
    equal = sum(1 for v in values if v == latest_vix)
    if len(values) > 1:
        vix_percentile = (less + (equal - 1) * 0.5) / (len(values) - 1) * 100
    else:
        vix_percentile = 100.0

    try:
        inputs = FearInputs(
            vix_value=latest_vix,
            vix_percentile=vix_percentile,
            vix_5d_change_pct=vix_5d_change_pct,
            taiwan_night_return_pct=None,
            taiwan_put_call_ratio=None,
        )
        result = score_fear(inputs)
        return result.score, "；".join(result.reasons)
    except Exception:
        return None, "VIX資料不足，尚無法計算全球風險因子"
