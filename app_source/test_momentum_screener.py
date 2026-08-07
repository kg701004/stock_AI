import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path

from momentum_screener import scan_momentum_leaders, MIN_HISTORY_DAYS, LOOKBACK_DAYS

DAYS = MIN_HISTORY_DAYS + 5
PEER_COUNT = 45  # + 1 winner (+ optional extras) clears the >= 40 liquid-universe floor


def _dates() -> list[str]:
    start = date(2022, 1, 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(DAYS)]


def _seed(connection: sqlite3.Connection, symbol: str, closes: list[float], volumes: list[int]) -> None:
    dates = _dates()
    for trading_date, close, volume in zip(dates, closes, volumes):
        connection.execute(
            "INSERT INTO daily_bars(symbol, trading_date, open_micros, high_micros, low_micros, close_micros, volume, source, published_at, import_checksum) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST', '2022-01-01T00:00:00+00:00', 'test')",
            (symbol, trading_date, int(close * 1_000_000), int(close * 1_000_000), int(close * 1_000_000), int(close * 1_000_000), volume),
        )


def _flat_peer(index: int) -> list[float]:
    # Unremarkable, roughly flat/mild drift over the whole history -- gives
    # scan_momentum_leaders a real cross-sectional baseline without any peer
    # itself having standout trailing-60-day momentum.
    return [100 + (index % 5) + 0.01 * i for i in range(DAYS)]


def _strong_rally(final_multiple: float) -> list[float]:
    # Flat for most of history, then a sharp rally in exactly the final
    # LOOKBACK_DAYS+1 window -- isolates trailing-60-day return as the
    # only thing that changed, independent of the stock's older history.
    flat_days = DAYS - (LOOKBACK_DAYS + 1)
    flat = [100.0] * flat_days
    rally = [100.0 + (100.0 * final_multiple - 100.0) * (i / LOOKBACK_DAYS) for i in range(LOOKBACK_DAYS + 1)]
    return flat + rally[1:]


class MomentumScreenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_momentum_screener.sqlite")
        self.database.unlink(missing_ok=True)
        self.connection = sqlite3.connect(self.database)
        self.connection.execute("""
            CREATE TABLE daily_bars (
                symbol TEXT NOT NULL, trading_date TEXT NOT NULL,
                open_micros INTEGER NOT NULL, high_micros INTEGER NOT NULL,
                low_micros INTEGER NOT NULL, close_micros INTEGER NOT NULL,
                volume INTEGER NOT NULL, source TEXT NOT NULL, published_at TEXT NOT NULL,
                import_checksum TEXT NOT NULL,
                PRIMARY KEY(symbol, trading_date, source)
            )
        """)
        for i in range(PEER_COUNT):
            _seed(self.connection, f"P{i:03d}", _flat_peer(i), [200_000] * DAYS)
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_a_strong_momentum_stock_is_returned_with_correct_fields(self) -> None:
        closes = _strong_rally(final_multiple=2.0)  # +100% over the lookback window
        _seed(self.connection, "WIN1", closes, [500_000] * DAYS)
        self.connection.commit()

        candidates = scan_momentum_leaders(self.database)
        symbols = {c.symbol for c in candidates}
        self.assertIn("WIN1", symbols)
        winner = next(c for c in candidates if c.symbol == "WIN1")
        self.assertAlmostEqual(winner.trailing_return_pct, 100.0, delta=1.0)
        self.assertGreater(winner.percentile_rank, 90.0)
        self.assertAlmostEqual(winner.current_price, closes[-1], places=2)
        self.assertGreaterEqual(winner.avg_dollar_volume, 20_000_000)
        # Strongest-first ordering.
        self.assertEqual(candidates[0].symbol, "WIN1")

    def test_a_flat_peer_is_not_in_the_top_quintile(self) -> None:
        closes = _strong_rally(final_multiple=2.0)
        _seed(self.connection, "WIN1", closes, [500_000] * DAYS)
        self.connection.commit()

        candidates = scan_momentum_leaders(self.database)
        self.assertNotIn("P000", {c.symbol for c in candidates})

    def test_a_declining_stock_is_excluded_even_with_ample_liquidity(self) -> None:
        closes = _strong_rally(final_multiple=0.5)  # -50% over the lookback window
        _seed(self.connection, "LOSER", closes, [500_000] * DAYS)
        self.connection.commit()

        candidates = scan_momentum_leaders(self.database)
        self.assertNotIn("LOSER", {c.symbol for c in candidates})

    def test_insufficient_liquidity_is_excluded(self) -> None:
        # Same strong rally shape, but priced/volumed too thin to clear
        # MIN_LIQUIDITY (NT$20M/day) -- isolates liquidity as the reason.
        closes = [c / 50 for c in _strong_rally(final_multiple=2.0)]  # ~2 to ~4
        _seed(self.connection, "THIN1", closes, [50_000] * DAYS)
        self.connection.commit()

        candidates = scan_momentum_leaders(self.database)
        self.assertNotIn("THIN1", {c.symbol for c in candidates})

    def test_insufficient_history_is_excluded(self) -> None:
        short_dates = _dates()[-(MIN_HISTORY_DAYS - 10):]
        closes = _strong_rally(final_multiple=2.0)[-(MIN_HISTORY_DAYS - 10):]
        for trading_date, close in zip(short_dates, closes):
            self.connection.execute(
                "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST', '2022-01-01T00:00:00+00:00', 'test')",
                ("SHORT1", trading_date, int(close * 1_000_000), int(close * 1_000_000), int(close * 1_000_000), int(close * 1_000_000), 500_000),
            )
        self.connection.commit()

        candidates = scan_momentum_leaders(self.database)
        self.assertNotIn("SHORT1", {c.symbol for c in candidates})

    def test_missing_database_returns_empty_list(self) -> None:
        self.assertEqual(scan_momentum_leaders(Path("data/does_not_exist_momentum.sqlite")), [])

    def test_too_small_a_liquid_universe_returns_empty_list(self) -> None:
        """Below the >= 40 floor used during the validating backtest, a
        percentile ranking isn't meaningful -- must degrade to empty, not
        fabricate a ranking over a handful of stocks."""
        tiny_db = Path("data/test_momentum_screener_tiny.sqlite")
        tiny_db.unlink(missing_ok=True)
        connection = sqlite3.connect(tiny_db)
        connection.execute("""
            CREATE TABLE daily_bars (
                symbol TEXT NOT NULL, trading_date TEXT NOT NULL,
                open_micros INTEGER NOT NULL, high_micros INTEGER NOT NULL,
                low_micros INTEGER NOT NULL, close_micros INTEGER NOT NULL,
                volume INTEGER NOT NULL, source TEXT NOT NULL, published_at TEXT NOT NULL,
                import_checksum TEXT NOT NULL,
                PRIMARY KEY(symbol, trading_date, source)
            )
        """)
        for i in range(5):
            _seed(connection, f"ONLY{i}", _flat_peer(i), [500_000] * DAYS)
        connection.commit()
        connection.close()

        self.assertEqual(scan_momentum_leaders(tiny_db), [])
        tiny_db.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
