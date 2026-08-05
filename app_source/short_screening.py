"""Independent short-candidate screening -- deliberately separate from weighted_analysis.

A low weighted_analysis score is NOT the same as "safe to short": shorting a
stock needs its own reasons (deteriorating fundamentals, confirmed technical
breakdown) and its own risk warnings (short squeeze), so this module never
reuses Assessment.final_score or inverts it.

Honest data-source scope (see 功能檢測與改善計畫.md section 9):
- Technical breakdown: fully implemented, reuses local daily_bars/technical_layers.
- Financial deterioration: implemented from what mops_financials actually
  stores (revenue/margin/debt_ratio trend across quarters). This is NOT a real
  Altman Z-Score or C-Score -- those need raw balance-sheet line items (total
  assets, total liabilities, working capital, retained earnings) that no
  importer in this codebase captures yet.
- 券資比 (margin-trading ratio), 借券 (securities lending) and 除權息強制回補
  dates (short-squeeze risk): NOT YET SUPPORTED. No importer exists for any of
  these data sources. margin_trading_signal() always reports missing data
  rather than fabricating a number, matching the same honesty convention
  already used for TPEx historical backfill and TAIFEX auto-import.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from database_utils import database_connection
from technical_factor import load_adjusted_bars
from technical_layers import calculate as calculate_layers

MINIMUM_TECHNICAL_BARS = 21
MINIMUM_FINANCIAL_PERIODS = 2


@dataclass(frozen=True, slots=True)
class ShortCandidateAssessment:
    symbol: str
    technical_score: float | None
    technical_notes: tuple[str, ...]
    financial_score: float | None
    financial_notes: tuple[str, ...]
    unsupported_warnings: tuple[str, ...]


def technical_breakdown_score(history_database: Path, symbol: str) -> tuple[float | None, tuple[str, ...]]:
    """Higher score = stronger confirmed bearish technical case (0-100)."""
    if not history_database.exists():
        return None, ("尚無本機歷史資料，無法計算技術面空頭訊號。",)
    try:
        bars = load_adjusted_bars(history_database, symbol)
    except Exception:
        return None, ("尚無本機歷史資料，無法計算技術面空頭訊號。",)
    if len(bars) < MINIMUM_TECHNICAL_BARS:
        return None, (f"歷史資料僅 {len(bars)} 筆，少於判斷所需的 {MINIMUM_TECHNICAL_BARS} 筆。",)
    layers = calculate_layers(bars)
    score, notes = 0.0, []
    if layers.regime == "空頭":
        score += 50.0
        notes.append("收盤價、20 日與 60 日均線呈空頭排列（死亡交叉型態）。")
    elif layers.ma60 is None:
        notes.append("資料少於 60 日，均線排列僅作低信心參考。")
    # Compare against support computed from the PRIOR 20 bars only (excluding
    # today) -- layers.support includes today's own low, which would make
    # "breaking below support" almost self-referentially impossible to trigger.
    prior_support = min(bar.low for bar in bars[-21:-1]) if len(bars) >= 21 else layers.support
    last_close = bars[-1].close
    if last_close < prior_support:
        breakdown_confirmed = layers.relative_volume >= 1.2
        score += 50.0 if breakdown_confirmed else 20.0
        notes.append(f"價格跌破前 20 日支撐 {prior_support:.2f}" + ("，且放量確認。" if breakdown_confirmed else "，但量能未確認，訊號較弱。"))
    else:
        notes.append(f"價格仍在前 20 日支撐 {prior_support:.2f} 之上，尚未跌破。")
    return round(min(score, 100.0), 2), tuple(notes)


def financial_deterioration_score(decision_database: Path, symbol: str) -> tuple[float | None, tuple[str, ...]]:
    """Higher score = more signs of deteriorating fundamentals across recent quarters (0-100).

    Not a real Altman Z-Score/C-Score -- see module docstring.
    """
    if not decision_database.exists():
        return None, ("尚無財報資料，無法計算財務惡化訊號。",)
    with database_connection(decision_database) as connection:
        try:
            rows = connection.execute(
                "SELECT revenue, gross_margin, operating_margin, roe, debt_ratio, source FROM mops_financials "
                "WHERE symbol = ? ORDER BY fiscal_year, fiscal_quarter",
                (symbol,),
            ).fetchall()
        except Exception:
            return None, ("尚無財報資料，無法計算財務惡化訊號。",)
    if len(rows) < MINIMUM_FINANCIAL_PERIODS:
        return None, (f"財報資料僅 {len(rows)} 期，少於判斷所需的 {MINIMUM_FINANCIAL_PERIODS} 期。",)
    previous, latest = rows[-2], rows[-1]
    fields = ("revenue", "gross_margin", "operating_margin", "roe", "debt_ratio")
    labels = {"revenue": "營收", "gross_margin": "毛利率", "operating_margin": "營益率", "roe": "ROE", "debt_ratio": "負債比"}
    score, notes = 0.0, []
    if previous[-1] != latest[-1]:
        # Different importers (automated TWSE sync vs. a manually uploaded MOPS
        # CSV) are not guaranteed to report figures in the same unit (e.g.
        # revenue in NT$ millions vs. thousands) -- comparing across sources
        # could silently fabricate a "worsened"/"improved" signal from a pure
        # unit mismatch. Only compare two periods reported by the same source.
        return None, (f"前後兩期財報來源不同（{previous[-1]} vs {latest[-1]}），單位可能不一致，暫不比較。",)
    for index, name in enumerate(fields):
        old_value, new_value = previous[index], latest[index]
        if old_value is None or new_value is None:
            continue
        worsened = new_value < old_value if name != "debt_ratio" else new_value > old_value
        if worsened:
            score += 20.0
            notes.append(f"{labels[name]}較前一期惡化（{old_value:.2f} → {new_value:.2f}）。")
    if not notes:
        notes.append("最近兩期財報數據不足以判斷惡化訊號。")
    return round(min(score, 100.0), 2), tuple(notes)


def margin_trading_signal(symbol: str) -> tuple[float | None, tuple[str, ...]]:
    """券資比／借券／軋空風險：目前系統沒有對應的資料匯入器，一律誠實回報缺資料。"""
    return None, ("券資比、借券賣出餘額與除權息強制回補期限尚未支援；本系統目前沒有對應的資料匯入器，無法評估軋空風險。",)


def assess_short_candidate(history_database: Path, decision_database: Path, symbol: str) -> ShortCandidateAssessment:
    technical_score, technical_notes = technical_breakdown_score(history_database, symbol)
    financial_score, financial_notes = financial_deterioration_score(decision_database, symbol)
    _unsupported_score, unsupported_notes = margin_trading_signal(symbol)
    return ShortCandidateAssessment(symbol, technical_score, technical_notes, financial_score, financial_notes, unsupported_notes)
