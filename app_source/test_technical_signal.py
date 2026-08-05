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
