#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
from pathlib import Path

# Add the parent directory of this script (app_source) to Python search path
script_dir = Path(__file__).resolve().parent
app_source_dir = script_dir.parent
sys.path.append(str(app_source_dir))

# If running from the repository root, change cwd to app_source/ so storage paths and config files resolve correctly.
if not Path("config/storage.json").exists() and (app_source_dir / "config" / "storage.json").exists():
    os.chdir(str(app_source_dir))

from storage_paths import storage_paths
import security_catalog
from technical_factor import load_adjusted_bars
import technical_validation

def main() -> None:
    # 1. Resolve history database path
    paths = storage_paths()
    history_database = paths["history_database"]

    # 2. Check if the database exists
    if not history_database.exists():
        print("資料不足，無法產生統計（資料庫檔案不存在）")
        sys.exit(0)

    # 3. List all symbols
    try:
        symbols = security_catalog.list_all_symbols(history_database)
    except Exception:
        print("資料不足，無法產生統計（無法讀取股票名錄）")
        sys.exit(0)

    if not symbols:
        print("資料不足，無法產生統計（股票名錄為空）")
        sys.exit(0)

    # 4. Iterate over symbols and screen those with at least 250 bars
    participating_symbols_count = 0
    total_signals = 0
    total_wins = 0.0  # sum(hit_rate_pct * signals)
    total_returns_sum = 0.0  # sum(average_return_pct * signals)

    # Baseline tracking
    baseline_total_trades = 0
    baseline_winning_trades = 0
    baseline_returns_sum = 0.0

    print(f"開始進行技術指標廣度回測，總共發現 {len(symbols)} 檔股票名錄...")

    for index, symbol in enumerate(symbols, 1):
        try:
            bars = load_adjusted_bars(history_database, symbol)
        except Exception:
            bars = []

        # Progress reporting every 50 stocks
        if index % 50 == 0:
            print(f"已處理 {index} / {len(symbols)} 檔股票...")

        if len(bars) < 250:
            continue

        participating_symbols_count += 1

        # Calculate out of sample performance
        try:
            _, out_of_sample = technical_validation.validate(bars, threshold=65, holding_days=5)
        except Exception:
            # If validation fails due to insufficient bars for validation or other issues, skip
            continue

        signals_count = out_of_sample.signals
        if signals_count > 0:
            total_signals += signals_count
            total_wins += out_of_sample.hit_rate_pct * signals_count
            total_returns_sum += out_of_sample.average_return_pct * signals_count

        # Calculate baseline performance: arbitrary buy on any day in out_of_sample period and hold for 5 days
        split = int(len(bars) * 0.7)
        for current in range(max(20, split), len(bars) - 5):
            ret_pct = (bars[current + 5].close / bars[current].close - 1) * 100
            baseline_total_trades += 1
            baseline_returns_sum += ret_pct
            if ret_pct > 0:
                baseline_winning_trades += 1

    # End progress print
    print(f"處理完成！共處理 {len(symbols)} 檔股票。")

    if participating_symbols_count == 0:
        print("資料不足，無法產生統計（沒有任何股票符合至少 250 個交易日的要求）")
        sys.exit(0)

    # Print final results
    print("=" * 40)
    print("【技術指標廣度回測結果 (holding_days=5)】")
    print(f"參與股票數: {participating_symbols_count}")
    print(f"總訊號數: {total_signals}")

    if total_signals > 0:
        weighted_win_rate = total_wins / total_signals
        weighted_return = total_returns_sum / total_signals
        print(f"加權平均勝率: {weighted_win_rate:.2f}%")
        print(f"加權平均報酬: {weighted_return:.2f}%")
    else:
        print("加權平均勝率: 0.00% (無任何交易訊號)")
        print("加權平均報酬: 0.00% (無任何交易訊號)")

    print("-" * 40)
    print("【基準線 (不設門檻、任意買進、抱5天)】")
    print(f"總交易數: {baseline_total_trades}")
    if baseline_total_trades > 0:
        baseline_win_rate = (baseline_winning_trades / baseline_total_trades) * 100.0
        baseline_avg_return = baseline_returns_sum / baseline_total_trades
        print(f"基準勝率: {baseline_win_rate:.2f}%")
        print(f"基準平均報酬: {baseline_avg_return:.2f}%")
    else:
        print("基準勝率: 0.00%")
        print("基準平均報酬: 0.00%")
    print("=" * 40)

if __name__ == "__main__":
    main()
