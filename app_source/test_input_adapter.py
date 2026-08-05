"""Tests for the manual CSV input path."""

import unittest
from pathlib import Path

from input_adapter import load_factor_csv


class InputAdapterTests(unittest.TestCase):
    def test_sample_csv_loads_two_assessments(self) -> None:
        rows = load_factor_csv(Path("data/sample_factor_scores.csv"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].symbol, "2330")
        self.assertEqual(rows[0].factors["technical"], 76)


if __name__ == "__main__":
    unittest.main()
