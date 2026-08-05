import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
from update_manager import run_startup_check

class StartupUpdateTests(unittest.TestCase):
 def test_skips_network_before_due_time_when_archive_is_valid(self):
  # 06:00 is before every source's scheduled time (VIX/TAIFEX 07:00, TWSE
  # 16:00, TPEx 16:30), so nothing should be due yet.
  now=datetime(2026,7,22,6,tzinfo=ZoneInfo('Asia/Taipei'))
  with patch('update_manager.list_statuses',return_value=[]), patch('update_manager.verify_archive',return_value=[]), patch('update_manager.run_manual_update') as update:
   result=run_startup_check(__import__('pathlib').Path('data/test_startup_history.sqlite'),__import__('pathlib').Path('data'),__import__('pathlib').Path('data'),now)
  self.assertIn('跳過下載',result); update.assert_not_called()
