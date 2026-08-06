"""法人動向 (institutional_flow) factor score, from real TWSE/TPEx three-major-
institutional-investor (三大法人) daily net buy/sell data.

Live-verified 2026-08-06: this project's own notes long assumed there was no
free public source for this factor -- that assumption was wrong. TWSE's T86
report and TPEx's tpex_3insti_daily_trading endpoint (see
external_data_importers.py) are both real, free and working, together
covering both markets.

Methodology (an honestly-disclosed simple heuristic, not a statistical
model, matching sentiment_fear.global_risk_factor_score's own framing):
sum the three-major total net buy/sell over the most recent locally stored
days for this symbol (at most INSTITUTIONAL_FLOW_WINDOW_DAYS), then express
that as a multiple of the symbol's own recent average daily share volume --
a stock-specific normalization, since raw share counts are meaningless
without knowing what's typical for that particular stock. That ratio is
linearly mapped onto a 0-100 score centered at 50.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from database_utils import database_connection

INSTITUTIONAL_FLOW_WINDOW_DAYS = 5
VOLUME_BASELINE_WINDOW_DAYS = 20
RATIO_TO_SCORE_SCALE = 25  # a cumulative net of +/-2x average daily volume maps to the 0/100 extreme


def institutional_flow_factor_score(database: Path, symbol: str) -> tuple[float | None, str]:
    if not database.exists():
        return None, "本機尚無歷史資料庫，無法計算法人動向分數。"
    try:
        with database_connection(database) as connection:
            flow_rows = connection.execute(
                "SELECT trading_date, total_net_shares FROM institutional_flow_history WHERE symbol = ? ORDER BY trading_date DESC LIMIT ?",
                (symbol, INSTITUTIONAL_FLOW_WINDOW_DAYS),
            ).fetchall()
            if not flow_rows:
                return None, "本機尚無此股票的三大法人買賣超資料（僅在系統接上該來源後才會逐日累積），暫不提供自動建議。"
            volume_rows = connection.execute(
                "SELECT volume FROM daily_bars WHERE symbol = ? ORDER BY trading_date DESC LIMIT ?",
                (symbol, VOLUME_BASELINE_WINDOW_DAYS),
            ).fetchall()
    except sqlite3.OperationalError:
        return None, "本機尚無此股票的三大法人買賣超資料（僅在系統接上該來源後才會逐日累積），暫不提供自動建議。"
    if not volume_rows:
        return None, "本機尚無此股票的日線成交量資料，無法將法人買賣超正規化，暫不提供自動建議。"

    cumulative_net = sum(row[1] for row in flow_rows)
    average_volume = sum(row[0] for row in volume_rows) / len(volume_rows)
    if average_volume <= 0:
        return None, "近期成交量資料異常（均量為零），無法計算法人動向分數。"

    ratio = cumulative_net / average_volume
    score = max(0.0, min(100.0, 50 + ratio * RATIO_TO_SCORE_SCALE))
    days = len(flow_rows)
    direction = "買超" if cumulative_net >= 0 else "賣超"
    note = (
        f"近 {days} 個交易日三大法人合計{direction} {abs(cumulative_net):,} 股，"
        f"約當近 {VOLUME_BASELINE_WINDOW_DAYS} 日均量的 {ratio:+.2f} 倍自動建議"
        "（僅供參考，可自行調整；資料才剛接上，天數越多、參考價值越高）。"
    )
    return round(score, 1), note
