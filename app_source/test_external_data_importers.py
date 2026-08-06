import sqlite3
import unittest
import unittest.mock
from datetime import date
from pathlib import Path
from external_data_importers import MopsFinancial, TaifexDaily, fetch_taifex_daily_report, import_mops, import_taifex, import_vix, parse_fred_vix_csv, parse_mops_csv, parse_mops_xbrl, parse_taifex_daily_report, InstitutionalFlow, fetch_twse_institutional_flow_report, parse_twse_institutional_flow_report, fetch_tpex_institutional_flow_report, parse_tpex_institutional_flow_report, import_institutional_flow

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

    def test_parse_taifex_daily_report_distinguishes_contract_months_and_sessions(self):
        """Regression guard: the real DailyMarketReportFut endpoint has
        multiple rows sharing the same Contract code (near-month, next-month,
        weeklies) -- if the contract month weren't folded into the stored id,
        taifex_daily_reports' (trading_date, contract, session) primary key
        would silently collapse them into one row via INSERT OR REPLACE."""
        raw = [
            {"Date": "20260805", "Contract": "TXF", "ContractMonth(Week)": "202608", "TradingSession": "一般",
             "Open": "22,000", "High": "22,100", "Low": "21,900", "Last": "22,050", "Volume": "1,234", "OpenInterest": "5,678"},
            {"Date": "20260805", "Contract": "TXF", "ContractMonth(Week)": "202609", "TradingSession": "一般",
             "Open": "22,200", "High": "22,300", "Low": "22,100", "Last": "22,250", "Volume": "10", "OpenInterest": "20"},
            {"Date": "20260805", "Contract": "TXF", "ContractMonth(Week)": "202608", "TradingSession": "盤後",
             "Open": "22,060", "High": "22,150", "Low": "22,000", "Last": "22,100", "Volume": "300", "OpenInterest": ""},
        ]
        parsed = parse_taifex_daily_report(raw)
        self.assertEqual(len(parsed), 3)
        ids = {(p.contract, p.session) for p in parsed}
        self.assertEqual(ids, {("TXF202608", "一般"), ("TXF202609", "一般"), ("TXF202608", "盤後")})
        near_month_day = next(p for p in parsed if p.contract == "TXF202608" and p.session == "一般")
        self.assertEqual(near_month_day.open_price, 22000.0)
        self.assertEqual(near_month_day.volume, 1234)
        night = next(p for p in parsed if p.session == "盤後")
        self.assertIsNone(night.open_interest)  # blank string must become None, not crash or become 0
        self.assertEqual(import_taifex(self.db, parsed), 3)

    def test_parse_taifex_daily_report_skips_malformed_rows(self):
        raw = [
            {"Date": "not-a-date", "Contract": "TXF", "ContractMonth(Week)": "202608", "TradingSession": "一般"},
            {"Date": "20260805", "Contract": "", "ContractMonth(Week)": "202608", "TradingSession": "一般"},
            {"Date": "20260805", "Contract": "TXF", "ContractMonth(Week)": "202608", "TradingSession": ""},
            "not a dict",
        ]
        self.assertEqual(parse_taifex_daily_report(raw), [])

    @unittest.mock.patch("urllib.request.urlopen")
    def test_fetch_taifex_daily_report_returns_the_raw_json_list(self, mock_urlopen):
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = b'[{"Date": "20260805", "Contract": "TXF"}]'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        records = fetch_taifex_daily_report()
        self.assertEqual(records, [{"Date": "20260805", "Contract": "TXF"}])

    @unittest.mock.patch("urllib.request.urlopen")
    def test_fetch_twse_index_valid(self, mock_urlopen):
        # Mock response for a valid trading day
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = (
            b'{"tables": [{"data": [["\xe7\x99\xbc\xe8\xa1\x8c\xe9\x87\x8f\xe5\x8a\xa0\xe6\xac\x8a\xe8\x82\xa1\xe5\x83\xb9\xe6\x8c\x87\xe6\x95\xb8", "48,081.45"]]}]}'
        )
        mock_urlopen.return_value.__enter__.return_value = mock_response

        from external_data_importers import fetch_twse_index
        val = fetch_twse_index(date(2026, 1, 5))
        self.assertEqual(val, 48081.45)

    @unittest.mock.patch("urllib.request.urlopen")
    def test_fetch_twse_index_non_trading_day(self, mock_urlopen):
        # Mock response for non-trading day (empty tables/data)
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = b'{"stat": "\xe6\xb2\x92\xe6\x9c\x89\xe8\xb3\x87\xe6\x96\x99", "tables": []}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        from external_data_importers import fetch_twse_index
        val = fetch_twse_index(date(2026, 1, 4))
        self.assertIsNone(val)

    @unittest.mock.patch("urllib.request.urlopen")
    def test_fetch_tpex_index(self, mock_urlopen):
        # Mock response with mixed date formats (with and without slash)
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = (
            b'[{"Date": "112/10/25", "Close": "218.45"}, {"Date": "1121026", "Close": "219.50"}]'
        )
        mock_urlopen.return_value.__enter__.return_value = mock_response

        from external_data_importers import fetch_tpex_index
        records = fetch_tpex_index()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0], (date(2023, 10, 25), 218.45))
        self.assertEqual(records[1], (date(2023, 10, 26), 219.50))

    def test_import_market_indices(self):
        from external_data_importers import import_market_indices
        records = [
            (date(2026, 1, 5), "TWSE", 48081.45),
            (date(2026, 1, 5), "TPEx", 218.45),
            (date(2026, 1, 6), "TWSE", 48100.00),
        ]
        count = import_market_indices(self.db, records)
        self.assertEqual(count, 3)

        # Query db to verify they are persisted
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute("SELECT trading_date, market, close_value FROM market_index_history ORDER BY trading_date, market").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], ("2026-01-05", "TPEx", 218.45))
        self.assertEqual(rows[1], ("2026-01-05", "TWSE", 48081.45))
        self.assertEqual(rows[2], ("2026-01-06", "TWSE", 48100.00))

    def test_parse_twse_institutional_flow_report_extracts_correct_columns(self):
        """Column layout confirmed live against a real T86 response: index 0
        is the symbol, 4/10/11/18 are foreign/trust/dealer/total net shares."""
        row = ["2330"] + ["0"] * 18
        row[4] = "1,234"
        row[10] = "-500"
        row[11] = "100"
        row[18] = "834"
        parsed = parse_twse_institutional_flow_report(date(2026, 8, 5), [row])
        self.assertEqual(parsed, [InstitutionalFlow(date(2026, 8, 5), "2330", 1234, -500, 100, 834)])

    def test_parse_twse_institutional_flow_report_skips_malformed_rows(self):
        raw = [
            ["00", "0", "0", "0", "1,000"] + ["0"] * 14,  # 2-digit symbol (ETF/warrant), not a stock
            ["2330"] + ["0"] * 3 + ["not-a-number"] + ["0"] * 14,  # unparseable numeric field
            ["2330"] + ["0"] * 5,  # too short (< 19 columns)
            "not a list",
        ]
        self.assertEqual(parse_twse_institutional_flow_report(date(2026, 8, 5), raw), [])

    @unittest.mock.patch("urllib.request.urlopen")
    def test_fetch_twse_institutional_flow_report_returns_data_rows(self, mock_urlopen):
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = b'{"stat": "OK", "date": "20260805", "data": [["2330", "0"]]}'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        records = fetch_twse_institutional_flow_report(date(2026, 8, 5))
        self.assertEqual(records, [["2330", "0"]])

    @unittest.mock.patch("urllib.request.urlopen")
    def test_fetch_twse_institutional_flow_report_non_trading_day_returns_empty(self, mock_urlopen):
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = b'{"stat": "\xe6\xb2\x92\xe6\x9c\x89\xe8\xb3\x87\xe6\x96\x99"}'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        records = fetch_twse_institutional_flow_report(date(2026, 8, 5))
        self.assertEqual(records, [])

    def test_parse_tpex_institutional_flow_report_extracts_correct_fields(self):
        """Field names confirmed live against a real tpex_3insti_daily_trading
        response -- note the stray space in the "Include" key, which is TPEx's
        own JSON key, not a typo here."""
        row = {
            "SecuritiesCompanyCode": "6182",
            "Date": "1150806",
            "ForeignInvestorsInclude MainlandAreaInvestors-Difference": "1,000",
            "SecuritiesInvestmentTrustCompanies-Difference": "-200",
            "Dealers-Difference": "50",
            "TotalDifference": "850",
        }
        parsed = parse_tpex_institutional_flow_report([row])
        self.assertEqual(parsed, [InstitutionalFlow(date(2026, 8, 6), "6182", 1000, -200, 50, 850)])

    def test_parse_tpex_institutional_flow_report_skips_malformed_rows(self):
        raw = [
            {"SecuritiesCompanyCode": "00", "Date": "1150806"},  # not a 4-digit stock symbol
            {"SecuritiesCompanyCode": "6182", "Date": "not-a-date"},  # unparseable date
            {"SecuritiesCompanyCode": "6182", "Date": "1150806"},  # missing all the numeric fields
            "not a dict",
        ]
        self.assertEqual(parse_tpex_institutional_flow_report(raw), [])

    @unittest.mock.patch("urllib.request.urlopen")
    def test_fetch_tpex_institutional_flow_report_returns_the_raw_json_list(self, mock_urlopen):
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = b'[{"SecuritiesCompanyCode": "6182"}]'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        records = fetch_tpex_institutional_flow_report()
        self.assertEqual(records, [{"SecuritiesCompanyCode": "6182"}])

    def test_import_institutional_flow(self):
        records = [
            InstitutionalFlow(date(2026, 8, 5), "2330", 1234, -500, 100, 834),
            InstitutionalFlow(date(2026, 8, 5), "6182", 1000, -200, 50, 850),
        ]
        self.assertEqual(import_institutional_flow(self.db, records), 2)
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute(
                "SELECT trading_date, symbol, foreign_net_shares, trust_net_shares, dealer_net_shares, total_net_shares "
                "FROM institutional_flow_history ORDER BY symbol"
            ).fetchall()
        self.assertEqual(rows, [
            ("2026-08-05", "2330", 1234, -500, 100, 834),
            ("2026-08-05", "6182", 1000, -200, 50, 850),
        ])
