"""Offline contract tests for TWSE public daily-data normalization."""

import unittest
from datetime import date, datetime, timezone

from twse_daily_importer import _legacy_to_openapi, extract_security_names, parse_daily_records


class TwseDailyImporterTests(unittest.TestCase):
    def test_extract_security_names_pulls_code_and_name_pairs(self) -> None:
        records = [{"Code": "2330", "Name": "台積電"}, {"Code": "XYZ", "Name": "not four digits"}, {"Code": "0050", "Name": ""}]
        self.assertEqual(extract_security_names(records), [("2330", "台積電")])

    def test_legacy_official_payload_is_normalized_for_fallback(self) -> None:
        payload={"fields":["證券代號","成交股數","開盤價","最高價","最低價","收盤價"],"data":[["2330","1,000","100","102","99","101"]]}
        row=_legacy_to_openapi(payload)[0]
        self.assertEqual(row["Code"],"2330")
        self.assertEqual(row["TradeVolume"],"1,000")
    def test_normalizes_english_field_names_and_commas(self) -> None:
        records = [{"Code": "2330", "OpeningPrice": "900.00", "HighestPrice": "925.00", "LowestPrice": "895.00", "ClosingPrice": "920.00", "TradeVolume": "32,100,000"}]
        bars = parse_daily_records(records, date(2026, 7, 21), datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc))
        self.assertEqual(bars[0].symbol, "2330")
        self.assertEqual(bars[0].volume, 32100000)
        self.assertEqual(bars[0].close_price, 920)

    def test_keeps_valid_four_digit_instruments_and_skips_incomplete_records(self) -> None:
        records = [
            {"Code": "0050", "OpeningPrice": "100", "HighestPrice": "101", "LowestPrice": "99", "ClosingPrice": "100", "TradeVolume": "1"},
            {"Code": "2330", "OpeningPrice": "--", "HighestPrice": "101", "LowestPrice": "99", "ClosingPrice": "100", "TradeVolume": "1"},
        ]
        bars = parse_daily_records(records, date(2026, 7, 21), datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc))
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].symbol, "0050")

    def test_prefers_the_records_own_date_over_the_caller_supplied_guess(self) -> None:
        """Regression guard: STOCK_DAY_ALL can lag behind wall-clock "today"
        by more than one day (live-confirmed 2026-08-07) -- the caller used
        to stamp every record with now.date() regardless, silently
        mislabeling a stale day's OHLC as today's. Each record carries its
        own ROC "Date" field ("1150805" = 2026-08-05); that must win over
        whatever date the caller guessed."""
        records = [{"Date": "1150805", "Code": "2330", "OpeningPrice": "2385.00", "HighestPrice": "2415.00", "LowestPrice": "2370.00", "ClosingPrice": "2405.00", "TradeVolume": "36,782,301"}]
        bars = parse_daily_records(records, date(2026, 8, 6), datetime(2026, 8, 6, 14, 30, tzinfo=timezone.utc))
        self.assertEqual(bars[0].trading_date, date(2026, 8, 5))

    def test_falls_back_to_caller_supplied_date_when_record_has_no_date_field(self) -> None:
        records = [{"Code": "2330", "OpeningPrice": "100", "HighestPrice": "101", "LowestPrice": "99", "ClosingPrice": "100", "TradeVolume": "1"}]
        bars = parse_daily_records(records, date(2026, 7, 21), datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc))
        self.assertEqual(bars[0].trading_date, date(2026, 7, 21))

    def test_requires_timezone(self) -> None:
        record = {"Code": "2330", "OpeningPrice": "100", "HighestPrice": "101", "LowestPrice": "99", "ClosingPrice": "100", "TradeVolume": "1"}
        with self.assertRaises(ValueError):
            parse_daily_records([record], date(2026, 7, 21), datetime(2026, 7, 21, 14, 30))


if __name__ == "__main__":
    unittest.main()
