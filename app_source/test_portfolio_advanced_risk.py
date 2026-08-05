import sqlite3, unittest
from datetime import datetime, timezone
from pathlib import Path
from portfolio import Position
from portfolio_advanced_risk import assess, compute_symbol_beta
from portfolio_risk import SecurityMetadata
from portfolio_risk_preferences import load, save
class AdvancedRiskTests(unittest.TestCase):
 def test_preferences_and_missing_history_are_explicit(self):
  root=Path('data/test_advanced_risk'); root.mkdir(parents=True,exist_ok=True); database=root/'decision.sqlite'; database.unlink(missing_ok=True)
  prefs=save(database,'Will','保守',{'window_days':60}); self.assertEqual(load(database,'Will')['profile'],'保守')
  p=[Position('Will','2330',100,100,120,datetime.now(timezone.utc))]; r=assess(root/'history.sqlite',p,{'2330':SecurityMetadata('2330','半導體',1.2)},prefs)
  self.assertIsNone(r.annualized_volatility_pct); self.assertTrue(r.warnings)


def _seed_daily_bars(database: Path, symbol: str, returns: list[float]) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol TEXT NOT NULL, trading_date TEXT NOT NULL,
                open_micros INTEGER NOT NULL, high_micros INTEGER NOT NULL,
                low_micros INTEGER NOT NULL, close_micros INTEGER NOT NULL,
                volume INTEGER NOT NULL, source TEXT NOT NULL, published_at TEXT NOT NULL,
                import_checksum TEXT NOT NULL,
                PRIMARY KEY(symbol, trading_date, source)
            )
        """)
        price = 100.0
        for day, r in enumerate([0.0] + returns):
            price *= (1 + r)
            date = f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}"
            connection.execute(
                "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST', ?, 'chk')",
                (symbol, date, int((price - 1) * 1_000_000), int((price + 1) * 1_000_000),
                 int((price - 2) * 1_000_000), int(price * 1_000_000), 1_000_000, f"{date}T13:30:00+08:00"),
            )
        connection.commit()
    finally:
        connection.close()


class CorrelationStressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/test_advanced_risk_correlation")
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "decision.sqlite"
        self.database.unlink(missing_ok=True)
        self.history = self.root / "history.sqlite"
        self.history.unlink(missing_ok=True)
        returns_a = [0.01 if i % 2 == 0 else -0.005 for i in range(31)]
        returns_b = [0.01 if i % 3 == 0 else (-0.007 if i % 3 == 1 else 0.003) for i in range(31)]
        _seed_daily_bars(self.history, "2330", returns_a)
        _seed_daily_bars(self.history, "2317", returns_b)
        self.positions = [
            Position("Will", "2330", 100, 100, 120, datetime.now(timezone.utc)),
            Position("Will", "2317", 100, 100, 100, datetime.now(timezone.utc)),
        ]
        self.metadata = {"2330": SecurityMetadata("2330", "半導體", 1.2), "2317": SecurityMetadata("2317", "電子製造", 1.0)}

    def _prefs(self, **overrides) -> dict:
        return save(self.database, "Will", "自訂", {
            "window_days": 60, "enable_dynamic_beta": False, "enable_var_es": False,
            "enable_stress_test": False, "enable_rebalance": False, "enable_correlation": True,
            "high_correlation_threshold": 0.75, **overrides,
        })

    def test_low_raw_correlation_is_measured(self) -> None:
        report = assess(self.history, self.positions, self.metadata, self._prefs(enable_correlation_stress=False))
        self.assertEqual(len(report.correlations), 1)
        self.assertLess(abs(report.correlations[0][2]), 0.75)

    def test_real_high_correlation_is_surfaced_as_warning(self) -> None:
        report = assess(self.history, self.positions, self.metadata, self._prefs(high_correlation_threshold=0.01, enable_correlation_stress=False))
        self.assertTrue(any("2330" in warning and "2317" in warning and "壓力情境" not in warning for warning in report.warnings))

    def test_stress_scenario_surfaces_a_labelled_warning_when_enabled(self) -> None:
        report = assess(self.history, self.positions, self.metadata, self._prefs(enable_correlation_stress=True, correlation_stress_shock_pct=100))
        self.assertTrue(any("壓力情境假設" in warning for warning in report.warnings))

    def test_stress_scenario_is_silent_when_disabled(self) -> None:
        report = assess(self.history, self.positions, self.metadata, self._prefs(enable_correlation_stress=False, correlation_stress_shock_pct=100))
        self.assertFalse(any("壓力情境假設" in warning for warning in report.warnings))


class ComputeSymbolBetaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history = Path("data/test_symbol_beta/history.sqlite")
        self.history.parent.mkdir(parents=True, exist_ok=True)
        self.history.unlink(missing_ok=True)

    def test_recovers_a_known_beta_from_a_constructed_linear_relationship(self) -> None:
        """Stock returns are exactly 1.5x the benchmark's every day (plus
        day-to-day variation so variance isn't degenerate) -- the regression
        must recover beta ~= 1.5, not some arbitrary/default number."""
        benchmark_returns = [0.01 if i % 2 == 0 else -0.006 for i in range(40)]
        stock_returns = [r * 1.5 for r in benchmark_returns]
        _seed_daily_bars(self.history, "0050", benchmark_returns)
        _seed_daily_bars(self.history, "9999", stock_returns)
        beta = compute_symbol_beta(self.history, "9999", benchmark_symbol="0050")
        self.assertIsNotNone(beta)
        self.assertAlmostEqual(beta, 1.5, places=2)

    def test_returns_none_when_aligned_history_is_below_the_minimum(self) -> None:
        _seed_daily_bars(self.history, "0050", [0.01] * 10)
        _seed_daily_bars(self.history, "9999", [0.01] * 10)
        self.assertIsNone(compute_symbol_beta(self.history, "9999", benchmark_symbol="0050"))

    def test_returns_none_for_an_unknown_symbol_or_missing_database(self) -> None:
        self.assertIsNone(compute_symbol_beta(self.history, "0000", benchmark_symbol="0050"))
        self.assertIsNone(compute_symbol_beta(Path("data/test_symbol_beta_never_created.sqlite"), "9999", benchmark_symbol="0050"))


if __name__ == "__main__":
    unittest.main()
