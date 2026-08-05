import unittest
from twse_financial_importer import parse_profitability_json

class TwseFinancialImporterTests(unittest.TestCase):
    def test_parses_actual_openapi_style_chinese_fields(self):
        payload = '[{"公司代號":"2330","年度":"2025","季別":"4","營業收入(百萬元)":"1,234","毛利率(%)(營業毛利)/(營業收入)":"56.7","營業利益率(%)(營業利益)/(營業收入)":"45.2"}]'.encode()
        row = parse_profitability_json(payload)[0]
        self.assertEqual((row.symbol,row.revenue,row.gross_margin,row.operating_margin), ("2330",1234.0,56.7,45.2))
    def test_rejects_wrong_json_shape(self):
        with self.assertRaises(ValueError): parse_profitability_json(b'{}')
