import unittest
from technical_layers import Bar, calculate
class TechnicalLayerTests(unittest.TestCase):
 def test_calculates_levels_and_volume(self):
  bars=[Bar(100+i,101+i,99+i,1000) for i in range(60)]; bars[-1]=Bar(159,160,158,2000); result=calculate(bars)
  self.assertGreater(result.atr,0); self.assertEqual(result.regime,"多頭"); self.assertEqual(result.relative_volume,2)
 def test_requires_enough_history(self):
  with self.assertRaises(ValueError): calculate([Bar(1,1,1,1)]*20)
