import unittest
import unittest.mock
from twse_financial_importer import parse_profitability_json, parse_general_industry_financials, fetch_general_industry_income_statement, fetch_general_industry_balance_sheet

class TwseFinancialImporterTests(unittest.TestCase):
    def test_parses_actual_openapi_style_chinese_fields(self):
        payload = '[{"公司代號":"2330","年度":"2025","季別":"4","營業收入(百萬元)":"1,234","毛利率(%)(營業毛利)/(營業收入)":"56.7","營業利益率(%)(營業利益)/(營業收入)":"45.2"}]'.encode()
        row = parse_profitability_json(payload)[0]
        self.assertEqual((row.symbol,row.revenue,row.gross_margin,row.operating_margin), ("2330",1234.0,56.7,45.2))
    def test_rejects_wrong_json_shape(self):
        with self.assertRaises(ValueError): parse_profitability_json(b'{}')

    def test_parse_general_industry_financials_computes_margins_roe_and_debt_ratio(self):
        """Formulas confirmed live 2026-08-07 against a real company (1215):
        gross_margin/operating_margin matched TWSE's own separately-published
        t187ap17_L ratios exactly (18.27%/9.26%)."""
        income = [{
            "公司代號": "1215", "年度": "115", "季別": "2",
            "營業收入": "14401643.00", "營業毛利（毛損）淨額": "2630870.00",
            "營業利益（損失）": "1333754.00", "淨利（淨損）歸屬於母公司業主": "954246.00",
        }]
        balance = [{
            "公司代號": "1215", "年度": "115", "季別": "2",
            "資產總計": "32784769.00", "負債總計": "21421630.00",
            "歸屬於母公司業主之權益合計": "10933923.00",
        }]
        parsed = parse_general_industry_financials(income, balance)
        self.assertEqual(len(parsed), 1)
        row = parsed[0]
        self.assertEqual((row.symbol, row.fiscal_year, row.fiscal_quarter, row.revenue), ("1215", 115, 2, 14401643.0))
        self.assertAlmostEqual(row.gross_margin, 18.267846, places=5)
        self.assertAlmostEqual(row.operating_margin, 9.261124, places=5)
        self.assertAlmostEqual(row.roe, 8.727389, places=5)
        self.assertAlmostEqual(row.debt_ratio, 65.340189, places=5)

    def test_parse_general_industry_financials_handles_missing_balance_sheet_row(self):
        """A symbol present in the income statement but absent from the
        balance sheet (mismatched filing timing between the two reports)
        must still produce revenue/margins, just without roe/debt_ratio --
        not silently drop the whole row."""
        income = [{
            "公司代號": "1215", "年度": "115", "季別": "2",
            "營業收入": "1000", "營業毛利（毛損）淨額": "200", "營業利益（損失）": "100",
            "淨利（淨損）歸屬於母公司業主": "50",
        }]
        parsed = parse_general_industry_financials(income, [])
        self.assertEqual(len(parsed), 1)
        self.assertIsNone(parsed[0].roe)
        self.assertIsNone(parsed[0].debt_ratio)
        self.assertEqual(parsed[0].gross_margin, 20.0)

    def test_parse_general_industry_financials_skips_non_stock_symbols(self):
        income = [{"公司代號": "ABC", "年度": "115", "季別": "2", "營業收入": "1000"}]
        self.assertEqual(parse_general_industry_financials(income, []), [])

    @unittest.mock.patch("urllib.request.urlopen")
    def test_fetch_general_industry_income_statement_returns_the_raw_json_list(self, mock_urlopen):
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = b'[{"\xe5\x85\xac\xe5\x8f\xb8\xe4\xbb\xa3\xe8\x99\x9f": "1215"}]'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        self.assertEqual(fetch_general_industry_income_statement(), [{"公司代號": "1215"}])

    @unittest.mock.patch("urllib.request.urlopen")
    def test_fetch_general_industry_balance_sheet_returns_the_raw_json_list(self, mock_urlopen):
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = b'[{"\xe5\x85\xac\xe5\x8f\xb8\xe4\xbb\xa3\xe8\x99\x9f": "1215"}]'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        self.assertEqual(fetch_general_industry_balance_sheet(), [{"公司代號": "1215"}])
