"""Smoke tests: each chart function must render into a real Tk widget without error."""
import unittest
try:
    import tkinter as tk
    from visualization import factor_heatmap, radar_chart, risk_gauge, sector_pie_chart
except Exception:
    tk = None


@unittest.skipIf(tk is None, "Tk is unavailable")
class VisualizationSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk(); self.root.withdraw()

    def tearDown(self) -> None:
        self.root.destroy()

    def test_radar_chart_renders(self) -> None:
        scores = {"technical": 76, "fundamentals": 81, "sentiment": 58}
        labels = {"technical": "技術面", "fundamentals": "基本面", "sentiment": "情緒指標"}
        canvas = radar_chart(self.root, scores, labels)
        self.assertIsNotNone(canvas)

    def test_sector_pie_chart_renders(self) -> None:
        canvas = sector_pie_chart(self.root, {"半導體": 55.0, "電子製造": 25.0, "其他": 20.0})
        self.assertIsNotNone(canvas)

    def test_risk_gauge_renders_at_each_band(self) -> None:
        for score in (10, 45, 85):
            canvas = risk_gauge(self.root, score)
            self.assertIsNotNone(canvas)

    def test_factor_heatmap_renders(self) -> None:
        rows = [("2330", {"technical": 76, "fundamentals": 81}), ("2317", {"technical": 61, "fundamentals": 70})]
        labels = {"technical": "技術面", "fundamentals": "基本面"}
        canvas = factor_heatmap(self.root, rows, labels)
        self.assertIsNotNone(canvas)

    def test_factor_heatmap_rejects_empty_rows(self) -> None:
        with self.assertRaises(ValueError):
            factor_heatmap(self.root, [], {})


if __name__ == "__main__":
    unittest.main()
