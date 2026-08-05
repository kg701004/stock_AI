#!/usr/bin/env python
"""Research script (not production code): does market breadth or VIX level
on day T have any real predictive power over broad forward stock returns,
using the app's own real local history data? Answers whether sentiment_fear.py
/ market_context.py are worth wiring in, or should stay unwired.

Not run by unittest; not imported by the app. Prints results to stdout.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage_paths import storage_paths

HOLDING_DAYS = 5
MIN_BARS = 250
LOOKBACK_TRADING_DATES = 750  # ~3 real years of distinct trading dates


def main() -> None:
    database = storage_paths()["history_database"]
    if not database.exists():
        print("資料不足：找不到 history.sqlite")
        return
    connection = sqlite3.connect(database)

    dates = [row[0] for row in connection.execute(
        "SELECT DISTINCT trading_date FROM daily_bars ORDER BY trading_date DESC LIMIT ?",
        (LOOKBACK_TRADING_DATES,),
    ).fetchall()]
    dates.reverse()
    if len(dates) < HOLDING_DAYS + 2:
        print("資料不足：可用交易日太少")
        return
    print(f"使用最近 {len(dates)} 個交易日（{dates[0]} ~ {dates[-1]}）")

    symbols = [row[0] for row in connection.execute(
        "SELECT symbol FROM daily_bars WHERE trading_date >= ? GROUP BY symbol HAVING COUNT(*) >= ?",
        (dates[0], MIN_BARS),
    ).fetchall()]
    print(f"參與股票數：{len(symbols)}")
    if len(symbols) < 10:
        print("資料不足：符合門檻的股票太少，結果不具參考性")
        return

    print("讀取每檔股票的收盤價序列...")
    closes_by_symbol: dict[str, dict[str, float]] = {}
    for index, symbol in enumerate(symbols, 1):
        rows = connection.execute(
            "SELECT trading_date, close_micros FROM daily_bars WHERE symbol = ? AND trading_date >= ? ORDER BY trading_date",
            (symbol, dates[0]),
        ).fetchall()
        closes_by_symbol[symbol] = {d: c / 1_000_000 for d, c in rows}
        if index % 20 == 0:
            print(f"  已讀取 {index}/{len(symbols)} 檔")

    date_index = {d: i for i, d in enumerate(dates)}

    print("計算每日市場寬度(advance/decline)與VIX水位...")
    breadth_ratio_by_date: dict[str, float] = {}
    for i in range(1, len(dates)):
        prev_date, curr_date = dates[i - 1], dates[i]
        advancing = declining = 0
        for closes in closes_by_symbol.values():
            prev_close, curr_close = closes.get(prev_date), closes.get(curr_date)
            if prev_close is None or curr_close is None:
                continue
            if curr_close > prev_close:
                advancing += 1
            elif curr_close < prev_close:
                declining += 1
        total = advancing + declining
        if total > 0:
            breadth_ratio_by_date[curr_date] = advancing / total

    vix_by_date: dict[str, float] = {}
    try:
        vix_rows = connection.execute(
            "SELECT trading_date, value FROM vix_history WHERE trading_date >= ? AND trading_date <= ? ORDER BY trading_date",
            (dates[0], dates[-1]),
        ).fetchall()
        vix_by_date = dict(vix_rows)
    except sqlite3.OperationalError:
        pass
    print(f"VIX資料涵蓋 {len(vix_by_date)}/{len(dates)} 個交易日")

    def forward_return_avg(as_of_date: str) -> float | None:
        idx = date_index.get(as_of_date)
        if idx is None or idx + HOLDING_DAYS >= len(dates):
            return None
        future_date = dates[idx + HOLDING_DAYS]
        returns = []
        for closes in closes_by_symbol.values():
            start, end = closes.get(as_of_date), closes.get(future_date)
            if start is not None and end is not None and start > 0:
                returns.append((end / start - 1) * 100)
        if not returns:
            return None
        return sum(returns) / len(returns)

    def bucket_report(label: str, score_by_date: dict[str, float]) -> None:
        pairs = [(score_by_date[d], forward_return_avg(d)) for d in score_by_date if d in score_by_date]
        pairs = [(s, r) for s, r in pairs if r is not None]
        if len(pairs) < 30:
            print(f"【{label}】資料不足（僅 {len(pairs)} 個有效交易日），略過")
            return
        pairs.sort(key=lambda x: x[0])
        n = len(pairs)
        low_third = pairs[: n // 3]
        high_third = pairs[-(n // 3):]
        all_returns = [r for _, r in pairs]

        def stats(rows: list[tuple[float, float]]) -> tuple[float, float, int]:
            rs = [r for _, r in rows]
            avg = sum(rs) / len(rs)
            hit = sum(1 for r in rs if r > 0) / len(rs) * 100
            return avg, hit, len(rs)

        low_avg, low_hit, low_n = stats(low_third)
        high_avg, high_hit, high_n = stats(high_third)
        base_avg = sum(all_returns) / len(all_returns)
        base_hit = sum(1 for r in all_returns if r > 0) / len(all_returns) * 100

        print(f"\n【{label}】共 {n} 個交易日，持有 {HOLDING_DAYS} 天後的全市場平均報酬")
        print(f"  基準線（全部交易日）        : 平均報酬 {base_avg:+.3f}%　正報酬比例 {base_hit:.1f}%　樣本數 {n}")
        print(f"  低分組（後1/3, n={low_n}）  : 平均報酬 {low_avg:+.3f}%　正報酬比例 {low_hit:.1f}%")
        print(f"  高分組（前1/3, n={high_n}） : 平均報酬 {high_avg:+.3f}%　正報酬比例 {high_hit:.1f}%")

    bucket_report("市場寬度(上漲家數比例，低分組=寬度弱，高分組=寬度強)", breadth_ratio_by_date)
    bucket_report("VIX水位(低分組=VIX低較不恐慌，高分組=VIX高較恐慌)", {d: v for d, v in vix_by_date.items() if d in date_index})

    connection.close()


if __name__ == "__main__":
    main()
