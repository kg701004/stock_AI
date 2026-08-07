"""散戶槓桿 (retail_leverage) factor score, from real TWSE/TPEx daily
融資融券餘額 (margin purchase / short sale balance) data.

Live-verified 2026-08-07: TWSE's legacy MI_MARGN report and TPEx's
tpex_mainboard_margin_balance endpoint (see external_data_importers.py) are
both real, free and working, together covering both markets. Margin
purchasing is overwhelmingly a retail tool (institutions rarely finance
positions this way), so net day-over-day margin-balance change is a
reasonable, honestly-disclosed proxy for retail long-leverage appetite --
distinct from the institutional_flow factor, which only covers the three
major institutional categories.

Methodology (an honestly-disclosed simple heuristic, not a statistical
model, matching institutional_flow.py's own framing): sum, over the most
recent locally stored days for this symbol (at most
RETAIL_LEVERAGE_WINDOW_DAYS), (margin balance net change - short balance net
change) -- i.e. net new leveraged-long positioning minus net new short
positioning -- then express that as a multiple of the symbol's own recent
average daily share volume. That ratio is linearly mapped onto a 0-100 score
centered at 50.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from database_utils import database_connection

RETAIL_LEVERAGE_WINDOW_DAYS = 5
VOLUME_BASELINE_WINDOW_DAYS = 20
RATIO_TO_SCORE_SCALE = 25  # a cumulative net of +/-2x average daily volume maps to the 0/100 extreme


def retail_leverage_factor_score(database: Path, symbol: str) -> tuple[float | None, str]:
    if not database.exists():
        return None, "本機尚無歷史資料庫，無法計算散戶槓桿分數。"
    try:
        with database_connection(database) as connection:
            leverage_rows = connection.execute(
                "SELECT trading_date, margin_net_change_shares, short_net_change_shares FROM margin_balance_history WHERE symbol = ? ORDER BY trading_date DESC LIMIT ?",
                (symbol, RETAIL_LEVERAGE_WINDOW_DAYS),
            ).fetchall()
            if not leverage_rows:
                return None, "本機尚無此股票的融資融券餘額資料（僅在系統接上該來源後才會逐日累積），暫不提供自動建議。"
            volume_rows = connection.execute(
                "SELECT volume FROM daily_bars WHERE symbol = ? ORDER BY trading_date DESC LIMIT ?",
                (symbol, VOLUME_BASELINE_WINDOW_DAYS),
            ).fetchall()
    except sqlite3.OperationalError:
        return None, "本機尚無此股票的融資融券餘額資料（僅在系統接上該來源後才會逐日累積），暫不提供自動建議。"
    if not volume_rows:
        return None, "本機尚無此股票的日線成交量資料，無法將融資融券餘額正規化，暫不提供自動建議。"

    cumulative_net = sum(row[1] - row[2] for row in leverage_rows)
    average_volume = sum(row[0] for row in volume_rows) / len(volume_rows)
    if average_volume <= 0:
        return None, "近期成交量資料異常（均量為零），無法計算散戶槓桿分數。"

    ratio = cumulative_net / average_volume
    score = max(0.0, min(100.0, 50 + ratio * RATIO_TO_SCORE_SCALE))
    days = len(leverage_rows)
    direction = "淨增加（偏多）" if cumulative_net >= 0 else "淨減少（偏空）"
    note = (
        f"近 {days} 個交易日融資餘額扣除融券餘額的淨變化為{direction} {abs(cumulative_net):,} 股，"
        f"約當近 {VOLUME_BASELINE_WINDOW_DAYS} 日均量的 {ratio:+.2f} 倍自動建議"
        "（僅供參考，可自行調整；融資融券主要反映散戶槓桿行為，非機構動向；資料才剛接上，天數越多、參考價值越高）。"
    )
    return round(score, 1), note
