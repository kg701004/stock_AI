"""Automatic VIX update is tested without external network access."""
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
from update_manager import list_statuses, run_manual_update

class ManualUpdateFlowTests(unittest.TestCase):
    def setUp(self):
        self.database=Path("data/test_manual_update.sqlite")
        connection=sqlite3.connect(self.database)
        try:
            connection.execute("DROP TABLE IF EXISTS data_update_status")
            connection.commit()
        finally:
            connection.close()
    def test_vix_automatic_source_imports_when_public_csv_is_available(self):
        source=next(item.source for item in list_statuses(self.database) if item.source.startswith("VIX"))
        with patch("update_manager.fetch_fred_vix_csv",return_value=b"observation_date,VIXCLS\n2026-07-21,18.5\n"):
            message=run_manual_update(source,self.database,Path("data/test_imports"),Path("data/test_archive"))
        status=next(item for item in list_statuses(self.database) if item.source==source)
        self.assertIn("VIX",message); self.assertEqual(status.status,"成功")
