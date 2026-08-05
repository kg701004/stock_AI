import time
import unittest

from technical_layers import Bar
from technical_signal import calculate
from technical_validation import validate


class TechnicalSignalTests(unittest.TestCase):
    def test_bullish_trend_and_volume_breakout_are_explainable(self) -> None:
        bars = [Bar(100 + index, 101 + index, 99 + index, 1000) for index in range(65)]
        for index in range(15):
            close = 158 + (index % 2) * 4
            bars[-15 + index] = Bar(close, close + 1, close - 1, 1000)
        bars[-1] = Bar(180, 181, 179, 2500)
        signal = calculate(bars)
        self.assertGreaterEqual(signal.score, 65)
        self.assertTrue(signal.breakout_confirmed)
        self.assertEqual(signal.status, "技術偏多")

    def test_overheated_signal_is_not_promoted_as_a_buy(self) -> None:
        bars = [Bar(100 + index * 3, 101 + index * 3, 99 + index * 3, 1000) for index in range(65)]
        signal = calculate(bars)
        self.assertLess(signal.score, 65)
        self.assertIn("過熱", " ".join(signal.reasons))

    def test_completely_flat_price_series_is_not_misread_as_overheated(self) -> None:
        """Regression test: a price series with zero movement (gains ==
        losses == 0, e.g. a thin/newly-listed stock or a data artifact
        repeating the last close) used to hit the same `losses == 0` branch
        as a genuine all-up move and return RSI 100 -- which calculate()
        then flags as "過熱" (overheated) and caps the score at 60, actively
        penalizing a stock that has shown zero momentum in either direction
        for the wrong reason."""
        bars = [Bar(100, 101, 99, 1000) for _ in range(65)]
        signal = calculate(bars)
        self.assertEqual(signal.rsi14, 50.0)
        self.assertNotIn("過熱", " ".join(signal.reasons))

    def test_fewer_than_21_bars_raises_instead_of_computing_a_score(self) -> None:
        bars = [Bar(100 + index, 101 + index, 99 + index, 1000) for index in range(20)]
        with self.assertRaises(ValueError):
            calculate(bars)

    def test_21_to_59_bars_warns_instead_of_faking_a_60_day_trend(self) -> None:
        """Regression guard for the "資料少於 60 日" branch (technical_signal.py
        calculate(), layers.ma60 is None): previously untested, so a
        regression here (e.g. someone "fixing" the None check) would go
        undetected even though it's exactly the history length a newly
        tracked/recently listed stock has."""
        bars = [Bar(100 + index, 101 + index, 99 + index, 1000) for index in range(45)]
        signal = calculate(bars)
        self.assertIsNotNone(signal.rsi14)  # RSI already computes at 45 bars; only ma60 is short
        self.assertIn("資料少於 60 日", " ".join(signal.warnings))

    def test_21_to_34_bars_warns_instead_of_faking_macd(self) -> None:
        """Regression guard for the "資料不足，未計算 MACD" branch
        (_macd_histogram requires 35+ closes): also previously untested."""
        bars = [Bar(100 + index, 101 + index, 99 + index, 1000) for index in range(30)]
        signal = calculate(bars)
        self.assertIsNone(signal.macd_histogram)
        self.assertIn("資料不足，未計算 MACD", " ".join(signal.warnings))
        self.assertIn("資料少於 60 日", " ".join(signal.warnings))

    def test_validation_keeps_later_period_out_of_sample(self) -> None:
        bars = [Bar(100 + index * 0.5, 101 + index * 0.5, 99 + index * 0.5, 1000) for index in range(80)]
        inside, outside = validate(bars, threshold=0, holding_days=5)
        self.assertGreater(inside.signals, 0)
        self.assertGreater(outside.signals, 0)

    def test_validate_stays_fast_on_a_real_world_history_length(self) -> None:
        """Regression guard: technical_layers.calculate()/_macd_histogram()
        used to redo full-history work on every one of validate()'s O(n)
        walk-forward steps (O(n^3) overall), which froze "技術面回測驗證"
        for minutes on a real stock with 2000+ backfilled bars (6182 has
        2441 in the real database). A small fixed bar count would never
        catch this -- it must be checked at realistic scale."""
        bars = [Bar(100 + (index % 37) * 0.3, 101 + (index % 37) * 0.3, 99 + (index % 37) * 0.3, 1000 + index % 500) for index in range(2500)]
        start = time.perf_counter()
        validate(bars)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 5.0, f"validate() took {elapsed:.1f}s on 2500 bars -- should be well under a second")


if __name__ == "__main__":
    unittest.main()
