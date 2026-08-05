"""Adversarial offline tests that approximate official-file import failures."""
import sqlite3
import unittest
from datetime import date
from pathlib import Path
from database_utils import database_connection

from external_data_importers import (
    MopsFinancial, TaifexDaily,
    import_mops, import_taifex, import_vix,
    parse_fred_vix_csv, parse_mops_csv, parse_mops_xbrl,
)


class ExternalDataEdgeCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Path("data/test_external_edges.sqlite")
        with database_connection(self.database) as connection:
            connection.execute("DROP TABLE IF EXISTS vix_history")
            connection.execute("DROP TABLE IF EXISTS mops_financials")
            connection.execute("DROP TABLE IF EXISTS taifex_daily_reports")

    def test_vix_accepts_bom_missing_observations_and_replaces_same_date(self) -> None:
        records = parse_fred_vix_csv("\ufeffobservation_date,VIXCLS\n2026-01-01,.\n2026-01-02, 18.50 \n".encode())
        import_vix(self.database, records); import_vix(self.database, [records[0].__class__(date(2026, 1, 2), 19.0)])
        with database_connection(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*), MAX(value) FROM vix_history").fetchone(), (1, 19.0))

    def test_vix_rejects_bad_date_and_no_numeric_values(self) -> None:
        with self.assertRaises(ValueError): parse_fred_vix_csv(b"observation_date,VIXCLS\n2026-01-01,.\n")
        with self.assertRaises(ValueError): parse_fred_vix_csv(b"observation_date,VIXCLS\nbad,10\n")

    def test_mops_accepts_chinese_headers_commas_percent_and_optional_blanks(self) -> None:
        payload = "公司代號,年度,季別,營業收入,每股盈餘,毛利率,負債比\n2330,2025,4,1,234.50,50.25,10.5%,42.0%\n"
        # Quote the comma-containing revenue as real CSV exporters do.
        payload = "公司代號,年度,季別,營業收入,每股盈餘,毛利率,負債比\n2330,2025,4,\"1,234.50\",50.25,10.5%,42.0%\n"
        row = parse_mops_csv(payload.encode())[0]
        self.assertEqual((row.revenue, row.gross_margin, row.debt_ratio), (1234.5, 10.5, 42.0))

    def test_mops_rejects_missing_identity_or_bad_numeric_value(self) -> None:
        with self.assertRaises(ValueError): parse_mops_csv("公司代號,年度,季別\n23A0,2025,1\n".encode())
        with self.assertRaises(ValueError): parse_mops_csv("symbol,fiscal_year,fiscal_quarter,eps\n2330,2025,1,nope\n".encode())

    def test_xbrl_namespace_and_malformed_xml(self) -> None:
        row = parse_mops_xbrl(b'<x:root xmlns:x="urn:test"><x:Revenue>100</x:Revenue><x:BasicEarningsPerShare>2.5</x:BasicEarningsPerShare></x:root>', "2330", 2025, 1)[0]
        self.assertEqual((row.revenue, row.eps), (100.0, 2.5))
        with self.assertRaises(Exception): parse_mops_xbrl(b"<bad>", "2330", 2025, 1)

    def test_mops_same_period_source_replaces_and_different_source_is_preserved(self) -> None:
        first = MopsFinancial("2330", 2025, 1, 1, None, None, None, None, None, "MOPS CSV")
        newer = MopsFinancial("2330", 2025, 1, 2, None, None, None, None, None, "MOPS CSV")
        xbrl = MopsFinancial("2330", 2025, 1, 3, None, None, None, None, None, "MOPS XBRL")
        import_mops(self.database, [first]); import_mops(self.database, [newer, xbrl])
        with database_connection(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*), MAX(revenue) FROM mops_financials").fetchone(), (2, 3.0))

    def test_taifex_day_and_after_hours_same_contract_are_distinct_and_idempotent(self) -> None:
        day = TaifexDaily(date(2026, 1, 2), "TX", "regular", 100, 110, 90, 105, 10, 20)
        night = TaifexDaily(date(2026, 1, 2), "TX", "after_hours", 105, 115, 100, 112, 11, 21)
        import_taifex(self.database, [day, night]); import_taifex(self.database, [night])
        with database_connection(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM taifex_daily_reports").fetchone()[0], 2)

    def test_transaction_rolls_back_if_one_mops_row_breaks_sql_constraint(self) -> None:
        good = MopsFinancial("2330", 2025, 1, 1, None, None, None, None, None, "MOPS CSV")
        bad = MopsFinancial(None, 2025, 1, 1, None, None, None, None, None, "MOPS CSV")  # type: ignore[arg-type]
        with self.assertRaises(sqlite3.IntegrityError): import_mops(self.database, [good, bad])
        with database_connection(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mops_financials").fetchone()[0], 0)
