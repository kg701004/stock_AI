#!/usr/bin/env python
"""Research script (not production code): pilot backtest for a candidate
market-wide screening rule -- cross-sectional relative-strength (momentum)
ranking -- using the app's own real local daily-bar history.

Methodology: on each test date (every 5th trading day from the reference
calendar, to keep runtime reasonable without materially changing the
conclusion), rank every liquid, sufficiently-long-tracked symbol by its
trailing 60-trading-day return. Bucket into top and bottom quintiles, then
measure each bucket's forward 60-trading-day return. Split chronologically
into an in-sample (first 70%) and out-of-sample (last 30%) period -- no
future-data leakage, matching technical_validation.validate()'s convention.

Universe: symbols with >= 750 distinct trading dates locally (~3 years),
AND average dollar volume >= NT$20M/day over the trailing 20 sessions as of
each test date (same liquidity floor as stock_screener.py's golden-cross
screen). This universe (~1,271 candidates, ~397 passing the liquidity
filter on a typical test date) is far broader than an earlier internal pilot
that used only the 50 symbols with the single deepest history -- that
smaller sample skews toward old, large, currently-listed companies
(survivorship bias) and was explicitly re-run at this larger scale before
building anything, precisely to check whether the initial finding held up.

Result (2026-08-07 run): out-of-sample top quintile n=37,937, 47.23%
positive, mean +7.24% (stdev 36.97%); bottom quintile n=37,898, 46.14%
positive, mean +2.01% (stdev 22.38%). Top-minus-bottom spread +5.23
percentage points, Welch's t-statistic 23.55 (|t| far beyond the
conventional 1.96 threshold for significance at n this large).

Caveats:
- Large stdev relative to the mean (top quintile: mean +7.24%, stdev
  36.97%) means this is a population-level average tilt, not a per-stock
  guarantee -- plenty of individual top-quintile stocks still lost money.
- Sampling every 5th date (not every date) and a single 60-day lookback/
  60-day holding combination were fixed BEFORE running this version, not
  chosen after peeking at results across a grid -- avoids the data-snooping
  trap flagged in backtest_golden_cross_screen.py. A genuine robustness
  check would still re-test with a different lookback (e.g. 20 or 120
  days) on a held-out later period before expanding reliance on this rule.
- Out-of-sample here corresponds to a specific, more recent slice of
  calendar time (the reference symbol's later ~30% of trading history), not
  a random sample of market regimes -- if that period happened to be an
  unusually strong bull run, the effect could be regime-specific rather
  than a stable structural momentum premium.

Not run by unittest; not imported by the app. Prints results to stdout.
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage_paths import storage_paths

MIN_HISTORY_DAYS = 750
MIN_AVG_DOLLAR_VOLUME = 20_000_000
LOOKBACK, HOLDING = 60, 60
TRAIN_FRACTION = 0.7
DATE_STEP = 5


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _stdev(values: list[float], m: float) -> float:
    if len(values) < 2:
        return 0.0
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _welch_t_test(a: list[float], b: list[float]) -> tuple[float, float]:
    ma, mb = _mean(a), _mean(b)
    sa, sb = _stdev(a, ma), _stdev(b, mb)
    se = math.sqrt(sa**2 / len(a) + sb**2 / len(b))
    return (ma - mb, float("inf")) if se == 0 else (ma - mb, (ma - mb) / se)


def main() -> None:
    db = storage_paths()["history_database"]

    with sqlite3.connect(db) as connection:
        candidates = [row[0] for row in connection.execute(
            "SELECT symbol, COUNT(DISTINCT trading_date) as n FROM daily_bars GROUP BY symbol HAVING n >= ? ORDER BY n DESC",
            (MIN_HISTORY_DAYS,),
        )]
    print(f"symbols with >= {MIN_HISTORY_DAYS} trading days: {len(candidates)}")

    sorted_dates, sorted_prices, sorted_dollar_vol, date_index = {}, {}, {}, {}
    with sqlite3.connect(db) as connection:
        for symbol in candidates:
            rows = connection.execute(
                "SELECT trading_date, close_micros, volume FROM daily_bars WHERE symbol = ? ORDER BY trading_date",
                (symbol,),
            ).fetchall()
            dates = [r[0] for r in rows]
            sorted_dates[symbol] = dates
            sorted_prices[symbol] = [r[1] / 1_000_000 for r in rows]
            sorted_dollar_vol[symbol] = [(r[1] / 1_000_000) * r[2] for r in rows]
            date_index[symbol] = {d: i for i, d in enumerate(dates)}

    reference_symbol = max(candidates, key=lambda s: len(sorted_dates[s]))
    reference_dates = sorted_dates[reference_symbol]
    print(f"reference calendar from {reference_symbol}: {len(reference_dates)} dates")

    def price_at(symbol: str, target_date: str) -> float | None:
        idx = date_index[symbol].get(target_date)
        return sorted_prices[symbol][idx] if idx is not None else None

    def avg_dollar_volume_trailing20(symbol: str, as_of_date: str) -> float | None:
        idx = date_index[symbol].get(as_of_date)
        if idx is None or idx < 19:
            return None
        window = sorted_dollar_vol[symbol][idx - 19: idx + 1]
        return sum(window) / len(window)

    split_index = int(len(reference_dates) * TRAIN_FRACTION)
    top_quintile: dict[str, list[float]] = {"in": [], "out": []}
    bottom_quintile: dict[str, list[float]] = {"in": [], "out": []}
    liquid_universe_sizes: list[int] = []

    for i in range(LOOKBACK, len(reference_dates) - HOLDING, DATE_STEP):
        ref_date, look_date, fwd_date = reference_dates[i], reference_dates[i - LOOKBACK], reference_dates[i + HOLDING]
        trailing_returns = []
        for symbol in candidates:
            p_ref, p_look = price_at(symbol, ref_date), price_at(symbol, look_date)
            if p_ref is None or p_look is None or p_look <= 0:
                continue
            avg_dv = avg_dollar_volume_trailing20(symbol, ref_date)
            if avg_dv is None or avg_dv < MIN_AVG_DOLLAR_VOLUME:
                continue
            trailing_returns.append((symbol, p_ref / p_look - 1))
        if len(trailing_returns) < 40:
            continue
        liquid_universe_sizes.append(len(trailing_returns))
        trailing_returns.sort(key=lambda x: x[1])
        n = len(trailing_returns)
        bucket = "in" if i < split_index else "out"
        for symbol, _ in trailing_returns[-(n // 5):]:
            p_ref, p_fwd = price_at(symbol, ref_date), price_at(symbol, fwd_date)
            if p_fwd is not None and p_ref:
                top_quintile[bucket].append((p_fwd / p_ref - 1) * 100)
        for symbol, _ in trailing_returns[: n // 5]:
            p_ref, p_fwd = price_at(symbol, ref_date), price_at(symbol, fwd_date)
            if p_fwd is not None and p_ref:
                bottom_quintile[bucket].append((p_fwd / p_ref - 1) * 100)

    print(f"avg liquid universe size per test date: {sum(liquid_universe_sizes) / len(liquid_universe_sizes):.0f} (of {len(candidates)} candidates)\n")

    def report(name: str, values: list[float]) -> str:
        if not values:
            return f"{name}: 0 samples"
        hit = sum(v > 0 for v in values) / len(values) * 100
        return f"{name}: n={len(values)}, {hit:.2f}% positive, mean={_mean(values):+.2f}%, stdev={_stdev(values, _mean(values)):.2f}%"

    print("=== Top quintile (strongest trailing 60-day momentum, liquidity-filtered) ===")
    print("  " + report("樣本內", top_quintile["in"]))
    print("  " + report("樣本外", top_quintile["out"]))
    print("=== Bottom quintile (weakest trailing 60-day momentum, liquidity-filtered) ===")
    print("  " + report("樣本內", bottom_quintile["in"]))
    print("  " + report("樣本外", bottom_quintile["out"]))

    diff, t_stat = _welch_t_test(top_quintile["out"], bottom_quintile["out"])
    print(f"\nOut-of-sample top-minus-bottom spread: {diff:+.2f}%, Welch t-statistic: {t_stat:.2f}")


if __name__ == "__main__":
    main()
