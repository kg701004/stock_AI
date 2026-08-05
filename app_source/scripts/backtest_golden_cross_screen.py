#!/usr/bin/env python
"""Research script (not production code): pilot backtest for a candidate
market-wide screening rule -- MA20 crossing above MA60 ("黃金交叉") -- using
the app's own real local daily-bar history, then progressively layers on
extra confirmation conditions to see which combination actually improves the
result rather than just assuming more filters = better.

Scoped to the subset of symbols that already have deep history locally
(>= MIN_BARS distinct trading dates); most of the ~2000 symbols in the
catalog only have a few days of recent snapshots so far (no broad historical
backfill has been run for the whole market yet). That means this result is a
quick sanity check on rule direction, NOT a representative full-market
success rate -- the sample skews toward whichever stocks happened to already
be tracked (holdings/watchlist) long enough to accumulate multi-year
history. A representative measurement requires running "全歷史資料下載" for
the whole catalog first.

No true benchmark-relative (vs TWSE index) comparison: market_index_history
currently only has 1 day of TWSE data (the fetcher was only wired up this
session), nowhere near enough to compute a fair vs-大盤 excess return for
events going back years. As a substitute, "relative strength" here means
outperforming the CROSS-SECTIONAL MEDIAN of this same 87-stock pool on the
same date (a peer-group proxy for "the market") -- an honest approximation,
not a true vs-TWSE-index measurement.

The final section is a parameter sweep over the RS lookback window and the
liquidity threshold: with a sample this small, picking whichever single
combination looks best is a classic overfitting trap (data-snooping across
many tested combinations). Reporting the whole grid, and looking for a
stable neighborhood rather than one isolated peak, is a basic hygiene check
before treating "the best cell" as a real edge.

Not run by unittest; not imported by the app. Prints results to stdout.
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage_paths import storage_paths
from technical_signal import _rsi

MIN_BARS = 250  # roughly one real trading year of distinct dates
MA_SHORT, MA_LONG = 20, 60
VOLUME_BASELINE_WINDOW = 20  # matches technical_layers.calculate's own relative_volume definition
SLOPE_LOOKBACK = 20  # ma60 today vs ma60 20 trading days ago -- is the long trend itself rising?
HORIZONS = (5, 10, 20, 60)
Series = tuple[list[str], list[float], list[float]]  # (dates, closes, volumes)


@dataclass(frozen=True, slots=True)
class Event:
    symbol: str
    index: int
    relative_volume: float | None
    ma_long_slope: float | None
    rsi14: float | None
    avg_dollar_volume: float | None
    returns: dict[int, float]  # horizon -> forward return %, only present when data exists


def _rolling_averages(values: list[float], period: int) -> list[float | None]:
    """averages[i] is the mean of values[i - period + 1 : i + 1], or None
    until enough history has accumulated. O(n) via a running window sum."""
    averages: list[float | None] = [None] * len(values)
    window_sum = 0.0
    for i, value in enumerate(values):
        window_sum += value
        if i >= period:
            window_sum -= values[i - period]
        if i >= period - 1:
            averages[i] = window_sum / period
    return averages


def _forward_return(closes: list[float], event_index: int, horizon: int) -> float | None:
    target = event_index + horizon
    if target >= len(closes):
        return None
    return (closes[target] / closes[event_index] - 1) * 100


def _load_series(connection: sqlite3.Connection, symbol: str) -> Series:
    rows = connection.execute(
        # GROUP BY handles the (symbol, trading_date, source) primary key
        # allowing more than one row per real calendar date -- averaging
        # is a safe representative price/volume since duplicate-source
        # rows for the same real trading day should already agree closely.
        "SELECT trading_date, AVG(close_micros), AVG(volume) FROM daily_bars WHERE symbol = ? GROUP BY trading_date ORDER BY trading_date",
        (symbol,),
    ).fetchall()
    dates = [d for d, _micros, _volume in rows]
    closes = [micros / 1_000_000 for _date, micros, _volume in rows]
    volumes = [vol for _date, _micros, vol in rows]
    return dates, closes, volumes


def _collect_events(series_by_symbol: dict[str, Series]) -> list[Event]:
    events: list[Event] = []
    for symbol, (_dates, closes, volumes) in series_by_symbol.items():
        ma_short = _rolling_averages(closes, MA_SHORT)
        ma_long = _rolling_averages(closes, MA_LONG)

        for i in range(1, len(closes)):
            prev_short, prev_long = ma_short[i - 1], ma_long[i - 1]
            curr_short, curr_long = ma_short[i], ma_long[i]
            if None in (prev_short, prev_long, curr_short, curr_long):
                continue
            if not (prev_short <= prev_long and curr_short > curr_long):
                continue

            baseline_start = i - VOLUME_BASELINE_WINDOW
            relative_volume = None
            if baseline_start >= 0:
                baseline = mean(volumes[baseline_start:i])
                if baseline:
                    relative_volume = volumes[i] / baseline

            ma_long_slope = None
            if i - SLOPE_LOOKBACK >= 0 and ma_long[i - SLOPE_LOOKBACK] is not None:
                ma_long_slope = curr_long - ma_long[i - SLOPE_LOOKBACK]

            rsi14 = _rsi(closes[: i + 1])

            avg_dollar_volume = None
            if i - VOLUME_BASELINE_WINDOW + 1 >= 0:
                window = range(i - VOLUME_BASELINE_WINDOW + 1, i + 1)
                avg_dollar_volume = mean(closes[j] * volumes[j] for j in window)

            returns = {h: r for h in HORIZONS if (r := _forward_return(closes, i, h)) is not None}
            events.append(Event(symbol, i, relative_volume, ma_long_slope, rsi14, avg_dollar_volume, returns))
    return events


def _peer_median_by_date(series_by_symbol: dict[str, Series], rs_lookback: int) -> dict[str, float]:
    """Cross-sectional median trailing rs_lookback-day return across this
    same stock pool, keyed by date -- the peer-group proxy for "the market"
    used in place of a real TWSE-index comparison (see module docstring)."""
    returns_by_date: dict[str, list[float]] = {}
    for dates, closes, _volumes in series_by_symbol.values():
        for i in range(rs_lookback, len(closes)):
            if closes[i - rs_lookback] <= 0:
                continue
            returns_by_date.setdefault(dates[i], []).append(closes[i] / closes[i - rs_lookback] - 1)
    return {date: median(returns) for date, returns in returns_by_date.items() if len(returns) >= 10}


def _relative_strengths(series_by_symbol: dict[str, Series], events: list[Event], rs_lookback: int) -> list[float | None]:
    """Relative strength for each event (aligned by position), for a given
    lookback window -- recomputed per sweep point since the peer median
    itself depends on the lookback."""
    peer_median = _peer_median_by_date(series_by_symbol, rs_lookback)
    results: list[float | None] = []
    for event in events:
        dates, closes, _volumes = series_by_symbol[event.symbol]
        i = event.index
        median_return = peer_median.get(dates[i])
        if median_return is None or i - rs_lookback < 0 or closes[i - rs_lookback] <= 0:
            results.append(None)
            continue
        own_return = closes[i] / closes[i - rs_lookback] - 1
        results.append(own_return - median_return)
    return results


def _report(label: str, events: list[Event]) -> None:
    print(f"\n=== {label}（樣本事件數：{len(events)}） ===")
    if not events:
        print("  無符合事件，略過。")
        return
    print(f"{'持有天數':>8} {'樣本數':>8} {'正報酬比例':>10} {'平均報酬':>10} {'中位數報酬':>10}")
    for horizon in HORIZONS:
        returns = [e.returns[horizon] for e in events if horizon in e.returns]
        if not returns:
            print(f"{horizon:>8} {'0':>8} {'—':>10} {'—':>10} {'—':>10}")
            continue
        win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
        print(f"{horizon:>8} {len(returns):>8} {win_rate:>9.1f}% {mean(returns):>+9.2f}% {median(returns):>+9.2f}%")


def _cell(events: list[Event], horizon: int) -> str:
    returns = [e.returns[horizon] for e in events if horizon in e.returns]
    if len(returns) < 15:
        return f"n={len(returns):<4}(樣本過小)"
    win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
    return f"n={len(returns):<4}勝{win_rate:4.1f}%中位{median(returns):+5.2f}%"


def _sweep(series_by_symbol: dict[str, Series], stage2_events: list[Event]) -> None:
    rs_lookbacks = (30, 60, 90)
    liquidity_thresholds = (0, 2_000_000, 5_000_000, 10_000_000, 20_000_000, 30_000_000)

    for horizon in (20, 60):
        print(f"\n=== 參數掃描：相對強勢比較期間 x 流動性門檻（持有{horizon}日） ===")
        header = f"{'RS期間\\流動性門檻':>14}" + "".join(f"{t / 1_000_000:>9.0f}M" for t in liquidity_thresholds)
        print(header)
        for rs_lookback in rs_lookbacks:
            relative_strengths = _relative_strengths(series_by_symbol, stage2_events, rs_lookback)
            rs_positive = [e for e, rs in zip(stage2_events, relative_strengths) if rs is not None and rs > 0]
            row = f"{rs_lookback:>12}日 "
            for threshold in liquidity_thresholds:
                filtered = [e for e in rs_positive if e.avg_dollar_volume is not None and e.avg_dollar_volume >= threshold]
                row += f" {_cell(filtered, horizon):>18}"
            print(row)


def main() -> None:
    database = storage_paths()["history_database"]
    if not database.exists():
        print("資料不足：找不到 history.sqlite")
        return
    connection = sqlite3.connect(database)

    symbols = [row[0] for row in connection.execute(
        "SELECT symbol FROM daily_bars GROUP BY symbol HAVING COUNT(DISTINCT trading_date) >= ?",
        (MIN_BARS,),
    ).fetchall()]
    print(f"符合門檻（>= {MIN_BARS} 個交易日）的股票數：{len(symbols)}")
    print("注意：這只是本地已有深度歷史的股票子集，尚未涵蓋全市場，結果僅供初步方向參考。")
    if len(symbols) < 5:
        print("資料不足：符合門檻的股票太少，結果不具參考性")
        return

    series_by_symbol = {symbol: _load_series(connection, symbol) for symbol in symbols}
    series_by_symbol = {s: v for s, v in series_by_symbol.items() if len(v[1]) >= MA_LONG + SLOPE_LOOKBACK + 2}
    all_events = _collect_events(series_by_symbol)
    connection.close()

    _report("Stage 0：黃金交叉（無過濾條件，基準線）", all_events)

    stage1 = [e for e in all_events if e.relative_volume is not None and e.relative_volume >= 1.2]
    _report("Stage 1：+ 帶量確認（當日量能 >= 20日均量的1.2倍）", stage1)

    stage2 = [e for e in stage1 if e.ma_long_slope is not None and e.ma_long_slope > 0]
    _report("Stage 2：+ 60日均線本身向上（非盤整期雜訊交叉）", stage2)

    print("\n提醒：以下是在小樣本上做參數掃描，目的是檢查Stage 5是不是穩定的區域、還是單點運氣——"
          "重點看「鄰近格子是否同樣好」，不要只挑單一最高分的格子。")
    _sweep(series_by_symbol, stage2)


if __name__ == "__main__":
    main()
