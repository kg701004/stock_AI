"""Offline contract tests for TPEx daily-data normalization."""

import unittest
from datetime import date, datetime, timezone

from tpex_daily_importer import extract_security_names, parse_daily_records


class TpexDailyImporterTests(unittest.TestCase):
    def test_extract_security_names_pulls_code_and_name_pairs(self) -> None:
        records = [{"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶"}]
        self.assertEqual(extract_security_names(records), [("6488", "環球晶")])

    def test_normalizes_common_tpex_field_names(self) -> None:
        records = [{"SecuritiesCompanyCode": "6488", "Open": "100", "High": "105", "Low": "98", "Close": "103", "Volume": "1,234,567"}]
        bars = parse_daily_records(records, date(2026, 7, 21), datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc))
        self.assertEqual((bars[0].symbol, bars[0].close_price, bars[0].volume), ("6488", 103, 1234567))

    def test_normalizes_real_tpex_openapi_field_names(self) -> None:
        # Real tpex_mainboard_daily_close_quotes response uses TradingShares, not
        # Volume/TradeVolume/成交股數 -- discovered by hitting the live API directly,
        # which previously made parse_daily_records silently drop every single row.
        records = [{"SecuritiesCompanyCode": "6182", "CompanyName": "合晶", "Open": "121.50", "High": "124.00", "Low": "114.00", "Close": "114.00", "TradingShares": "20847262"}]
        bars = parse_daily_records(records, date(2026, 7, 24), datetime(2026, 7, 24, 14, 30, tzinfo=timezone.utc))
        self.assertEqual((bars[0].symbol, bars[0].close_price, bars[0].volume), ("6182", 114.0, 20847262))

    def test_rejects_naive_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            parse_daily_records([], date(2026, 7, 21), datetime(2026, 7, 21, 14, 30))


if __name__ == "__main__":
    unittest.main()
