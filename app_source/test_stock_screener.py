import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path

from stock_screener import scan_market, RECENCY_WINDOW

DAYS = 130
PEER_COUNT = 12


def _dates() -> list[str]:
    start = date(2024, 1, 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(DAYS)]


def _seed(connection: sqlite3.Connection, symbol: str, closes: list[float], volumes: list[int]) -> None:
    dates = _dates()
    for trading_date, close, volume in zip(dates, closes, volumes):
        connection.execute(
            "INSERT INTO daily_bars(symbol, trading_date, open_micros, high_micros, low_micros, close_micros, volume, source, published_at, import_checksum) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST', '2024-01-01T00:00:00+00:00', 'test')",
            (symbol, trading_date, int(close * 1_000_000), int(close * 1_000_000), int(close * 1_000_000), int(close * 1_000_000), volume),
        )


def _gentle_peer(index: int) -> list[float]:
    # A steady, unremarkable rise -- MA20 stays above MA60 throughout (no
    # fresh crossover), so peers never qualify as candidates themselves in
    # tests that don't care about relative strength; they only exist to give
    # scan_market a real cross-sectional baseline.
    return [100 + (index % 5) + i * 0.1 for i in range(DAYS)]


def _phase(n: int, start: float, end: float) -> list[float]:
    return [start + (end - start) * (i / max(1, n - 1)) for i in range(n)]


def _late_cross_shape(d1: int, d2: int, d3: int, decline_to: float, rally_to: float, dip_to: float, final_to: float) -> list[float]:
    """decline -> rally -> small dip -> final push, tuned (verified
    numerically against stock_screener's own rolling-average helpers) so the
    FRESH MA20/MA60 crossover lands near the very end of a 130-day series --
    a single rally alone tends to cross much earlier (MA20 reacts faster
    than MA60), so a genuine "late" crossover with an already-positive
    60-day trend needs this dip-then-recover shape instead of a straight
    decline-then-rally."""
    closes = _phase(d1, 100.0, decline_to)
    closes += _phase(d2 - d1, decline_to, rally_to)[1:]
    closes += _phase(d3 - d2, rally_to, dip_to)[1:]
    closes += _phase(DAYS - d3, dip_to, final_to)[1:]
    if len(closes) < DAYS:
        closes += [closes[-1]] * (DAYS - len(closes))
    return closes[:DAYS]


def _win1_shape() -> list[float]:
    """Verified: fresh crossover at index 125 (4 trading days before the end
    of a 130-day series), 60-day-slope +2.42, trailing-30-day return +26%."""
    return _late_cross_shape(d1=30, d2=70, d3=110, decline_to=70.0, rally_to=130.0, dip_to=100.0, final_to=140.0)


def _mild_shape() -> list[float]:
    """Same crossover mechanics as _win1_shape, but a much milder final
    push: verified crossover at index 126, slope +0.12, trailing-30-day
    return only +11% -- used to test the relative-strength filter against
    peers rallying harder over the same window."""
    return _late_cross_shape(d1=30, d2=70, d3=110, decline_to=90.0, rally_to=115.0, dip_to=100.0, final_to=116.0)


def _stale_shape() -> list[float]:
    """The same decline/rally/dip/push shape as _win1_shape, compressed into
    the first 90 days and then held flat -- verified crossover at index 84,
    45 trading days before the end of the series, comfortably outside any
    reasonable RECENCY_WINDOW."""
    d1, d2, d3, active_days = 21, 48, 76, 90
    closes = _phase(d1, 100.0, 70.0)
    closes += _phase(d2 - d1, 70.0, 130.0)[1:]
    closes += _phase(d3 - d2, 130.0, 100.0)[1:]
    closes += _phase(active_days - d3, 100.0, 140.0)[1:]
    closes += [closes[-1]] * (DAYS - len(closes))
    return closes[:DAYS]


class StockScreenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_stock_screener.sqlite")
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
            _seed(self.connection, f"P{i:03d}", _gentle_peer(i), [200_000] * DAYS)
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_a_fresh_qualifying_setup_is_returned_with_correct_fields(self) -> None:
        closes = _win1_shape()
        volumes = [200_000] * (DAYS - 10) + [900_000] * 10  # volume spike covering the crossover day
        _seed(self.connection, "WIN1", closes, volumes)
        self.connection.commit()

        candidates = scan_market(self.database)
        symbols = {c.symbol for c in candidates}
        self.assertIn("WIN1", symbols)
        winner = next(c for c in candidates if c.symbol == "WIN1")
        self.assertLessEqual(winner.days_since_signal, RECENCY_WINDOW - 1)
        self.assertGreaterEqual(winner.relative_volume, 1.2)
        self.assertGreater(winner.ma_long_slope_pct, 0)
        self.assertGreater(winner.relative_strength_pct, 0)
        self.assertGreaterEqual(winner.avg_dollar_volume, 20_000_000)
        self.assertAlmostEqual(winner.current_price, closes[-1], places=2)

    def test_a_crossover_without_volume_confirmation_is_excluded(self) -> None:
        closes = _win1_shape()
        volumes = [200_000] * DAYS  # no spike at all
        _seed(self.connection, "NOVOL", closes, volumes)
        self.connection.commit()

        candidates = scan_market(self.database)
        self.assertNotIn("NOVOL", {c.symbol for c in candidates})

    def test_a_stale_crossover_outside_the_recency_window_is_excluded(self) -> None:
        """Same qualifying setup as the positive case, but the crossover
        happened well before RECENCY_WINDOW -- must not still show up as a
        "fresh" signal just because it once matched."""
        closes = _stale_shape()
        volumes = [200_000] * DAYS
        for offset in range(-2, 3):
            volumes[84 + offset] = 900_000
        _seed(self.connection, "STALE", closes, volumes)
        self.connection.commit()

        candidates = scan_market(self.database)
        self.assertNotIn("STALE", {c.symbol for c in candidates})
        self.assertGreater(RECENCY_WINDOW, 0)  # sanity: the constant this test relies on is non-trivial

    def test_insufficient_liquidity_is_excluded(self) -> None:
        # Same shape as the winner, but at a price/volume level whose dollar
        # turnover never reaches MIN_LIQUIDITY -- volume still spikes
        # (relative_volume passes) so liquidity is isolated as the reason.
        closes = [c / 20 for c in _win1_shape()]  # ~3.5 to ~6.9
        volumes = [100_000] * (DAYS - 10) + [300_000] * 10
        _seed(self.connection, "THIN1", closes, volumes)
        self.connection.commit()

        candidates = scan_market(self.database)
        self.assertNotIn("THIN1", {c.symbol for c in candidates})

    def test_underperforming_the_peer_group_is_excluded(self) -> None:
        """A crossover that technically fires, with real volume/slope/
        liquidity all passing, but this stock's own trailing 30-day return
        is weaker than the peer group's -- relative_strength <= 0."""
        # Replace the gentle peers with ones rallying hard over the same
        # window (same shape family as the winner, +26% trailing return),
        # so their median clearly exceeds this stock's much milder +11%.
        for i in range(PEER_COUNT):
            self.connection.execute(f"DELETE FROM daily_bars WHERE symbol = 'P{i:03d}'")
            _seed(self.connection, f"P{i:03d}", _win1_shape(), [200_000] * DAYS)

        mild_closes = _mild_shape()
        volumes = [200_000] * (DAYS - 10) + [900_000] * 10
        _seed(self.connection, "WEAK1", mild_closes, volumes)
        self.connection.commit()

        candidates = scan_market(self.database)
        self.assertNotIn("WEAK1", {c.symbol for c in candidates})


if __name__ == "__main__":
    unittest.main()
