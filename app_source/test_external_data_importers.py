import sqlite3
import unittest
from datetime import date
from pathlib import Path
from external_data_importers import MopsFinancial, TaifexDaily, import_mops, import_taifex, import_vix, parse_fred_vix_csv, parse_mops_csv, parse_mops_xbrl

class ExternalImporterTests(unittest.TestCase):
    def setUp(self): self.db = Path("data/test_external.sqlite")
    def test_fred_vix_skips_missing_values_and_imports(self):
        rows = parse_fred_vix_csv(b"observation_date,VIXCLS\n2026-01-01,.\n2026-01-02,18.5\n")
        self.assertEqual(import_vix(self.db, rows), 1)
    def test_mops_csv_and_xbrl_are_normalized(self):
        csv_rows = parse_mops_csv("公司代號,年度,季別,營業收入,每股盈餘\n2330,2025,1,1000,10.5\n".encode())
        xml_rows = parse_mops_xbrl(b"<root><Revenue>2000</Revenue><BasicEarningsPerShare>20</BasicEarningsPerShare></root>", "2330", 2025, 2)
        self.assertEqual(import_mops(self.db, csv_rows + xml_rows), 2)
    def test_taifex_night_session_is_persisted(self):
        self.assertEqual(import_taifex(self.db, [TaifexDaily(date(2026,1,2), "TX", "after_hours", 1,2,1,2,100,200)]), 1)
