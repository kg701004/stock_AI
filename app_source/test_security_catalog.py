"""Tests for the SQLite-backed securities master that replaced the 3-entry catalog."""

import sqlite3
import unittest
from pathlib import Path

from security_catalog import is_etf, list_all_symbols, load_security_metadata, lookup_market, parse_company_basic_info, resolve, upsert_from_daily_snapshot, upsert_sectors


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


class SecurityCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("test_security_catalog.sqlite")
        if self.database.exists():
            self.database.unlink()

    def tearDown(self) -> None:
        if self.database.exists():
            self.database.unlink()

    def test_empty_catalog_rejects_with_actionable_message(self) -> None:
        with self.assertRaises(ValueError):
            resolve(self.database, "2330")

    def test_daily_snapshot_grows_catalog_and_resolves_by_code_or_name(self) -> None:
        upsert_from_daily_snapshot(self.database, [("2330", "台積電"), ("2317", "鴻海")], "TWSE", "2026-07-24T00:00:00+08:00")
        self.assertEqual(resolve(self.database, "2330"), "2330")
        self.assertEqual(resolve(self.database, "台積電"), "2330")
        self.assertEqual(resolve(self.database, "鴻海"), "2317")

    def test_ambiguous_partial_name_is_rejected(self) -> None:
        upsert_from_daily_snapshot(self.database, [("2330", "台積電"), ("3105", "台積電子")], "TWSE", "2026-07-24T00:00:00+08:00")
        with self.assertRaises(ValueError):
            resolve(self.database, "台積")

    def test_rerunning_snapshot_refreshes_without_duplicating(self) -> None:
        upsert_from_daily_snapshot(self.database, [("2330", "台積電")], "TWSE", "2026-07-24T00:00:00+08:00")
        upsert_from_daily_snapshot(self.database, [("2330", "台積電")], "TWSE", "2026-07-25T00:00:00+08:00")
        from database_utils import database_connection
        with database_connection(self.database) as connection:
            count = connection.execute("SELECT COUNT(*) FROM securities WHERE symbol='2330'").fetchone()[0]
        self.assertEqual(count, 1)

    def test_sectors_are_translated_from_industry_codes(self) -> None:
        upsert_from_daily_snapshot(self.database, [("2330", "台積電")], "TWSE", "2026-07-24T00:00:00+08:00")
        upsert_sectors(self.database, [("2330", "24")])
        from database_utils import database_connection
        with database_connection(self.database) as connection:
            sector = connection.execute("SELECT sector FROM securities WHERE symbol='2330'").fetchone()[0]
        self.assertEqual(sector, "半導體業")

    def test_parse_company_basic_info_extracts_code_and_industry(self) -> None:
        records = [{"公司代號": "1101", "公司名稱": "臺灣水泥股份有限公司", "產業別": "01"}]
        self.assertEqual(parse_company_basic_info(records), [("1101", "01")])

    def test_lookup_market_returns_catalogued_market_or_none(self) -> None:
        upsert_from_daily_snapshot(self.database, [("6182", "合晶")], "TPEx", "2026-07-24T00:00:00+08:00")
        self.assertEqual(lookup_market(self.database, "6182"), "TPEx")
        self.assertIsNone(lookup_market(self.database, "9999"))

    def test_load_security_metadata_defaults_every_beta_to_neutral(self) -> None:
        upsert_from_daily_snapshot(self.database, [("2330", "台積電")], "TWSE", "2026-07-24T00:00:00+08:00")
        metadata = load_security_metadata(self.database)
        self.assertEqual(metadata["2330"].beta, 1.0)

    def test_load_security_metadata_computes_a_real_beta_for_requested_symbols(self) -> None:
        upsert_from_daily_snapshot(self.database, [("2330", "台積電"), ("0050", "元大台灣50")], "TWSE", "2026-07-24T00:00:00+08:00")
        benchmark_returns = [0.01 if i % 2 == 0 else -0.006 for i in range(40)]
        _seed_daily_bars(self.database, "0050", benchmark_returns)
        _seed_daily_bars(self.database, "2330", [r * 1.5 for r in benchmark_returns])
        # Only "2330" is requested -- "0050" itself must stay at the neutral
        # default (opt-in scoping, not silently computed for every catalogued symbol).
        metadata = load_security_metadata(self.database, symbols=["2330"])
        self.assertAlmostEqual(metadata["2330"].beta, 1.5, places=2)
        self.assertEqual(metadata["0050"].beta, 1.0)

    def test_load_security_metadata_falls_back_to_neutral_without_enough_aligned_history(self) -> None:
        upsert_from_daily_snapshot(self.database, [("2330", "台積電")], "TWSE", "2026-07-24T00:00:00+08:00")
        metadata = load_security_metadata(self.database, symbols=["2330"])
        self.assertEqual(metadata["2330"].beta, 1.0)  # no daily_bars seeded -> honest fallback, not a crash

    def test_list_all_symbols_returns_every_catalogued_symbol_sorted(self) -> None:
        self.assertEqual(list_all_symbols(self.database), [])  # fresh install -- not an error
        upsert_from_daily_snapshot(self.database, [("2330", "台積電"), ("1101", "台泥")], "TWSE", "2026-07-24T00:00:00+08:00")
        upsert_from_daily_snapshot(self.database, [("6182", "合晶")], "TPEx", "2026-07-24T00:00:00+08:00")
        self.assertEqual(list_all_symbols(self.database), ["1101", "2330", "6182"])

    def test_is_etf_uses_the_00_prefix_convention(self) -> None:
        self.assertTrue(is_etf("0050"))
        self.assertTrue(is_etf("00878"))
        self.assertFalse(is_etf("2330"))


if __name__ == "__main__":
    unittest.main()
