"""Tests for the KGI fee-reference accept/reject branching logic.

This module's actual network integration was independently verified live
against the real KGI page during this session (`verify_and_cache` returned
"已連線確認" for real, twice). These tests instead cover the branching logic
itself with injected HTTP responses -- accept/reject/exception paths -- so
the suite doesn't depend on KGI's page staying reachable to catch a logic
regression.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from broker_fee_sync import verify_and_cache


class BrokerFeeSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = Path("test_broker_fee_sync_cache.json")
        if self.cache.exists():
            self.cache.unlink()

    def tearDown(self) -> None:
        if self.cache.exists():
            self.cache.unlink()

    def _mock_response(self, body: bytes):
        response = MagicMock()
        response.read.return_value = body
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response

    def test_page_with_all_required_rates_is_accepted_and_cached(self) -> None:
        page = "手續費 0.1425% 起，證交稅 0.3%，當沖 0.15%，ETF 0.1%，最低 20 元".encode("utf-8")
        with patch("broker_fee_sync.urlopen", return_value=self._mock_response(page)):
            result = verify_and_cache(self.cache)
        self.assertIn("已連線確認", result)
        self.assertTrue(self.cache.exists())
        cached = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(cached["rates"]["fee_rate"], 0.001425)

    def test_page_missing_a_required_rate_is_rejected_without_caching(self) -> None:
        # Missing the "0.1%" (ETF tax) substring -- format changed or page is wrong.
        page = "手續費 0.1425% 起，證交稅 0.3%，當沖 0.15%，最低 20 元".encode("utf-8")
        with patch("broker_fee_sync.urlopen", return_value=self._mock_response(page)):
            result = verify_and_cache(self.cache)
        self.assertIn("未完整辨識", result)
        self.assertFalse(self.cache.exists())

    def test_network_failure_degrades_honestly_without_raising(self) -> None:
        with patch("broker_fee_sync.urlopen", side_effect=TimeoutError("simulated timeout")):
            result = verify_and_cache(self.cache)
        self.assertIn("未確認", result)
        self.assertIn("TimeoutError", result)
        self.assertFalse(self.cache.exists())


if __name__ == "__main__":
    unittest.main()
