import unittest
from watchlist_decision import calculate, evaluate
class WatchlistDecisionTests(unittest.TestCase):
 def test_levels_depend_on_market_not_reference(self):
  a=calculate(100,120,70,30); b=calculate(80,120,70,30); self.assertEqual((a.target_price,a.stop_price),(b.target_price,b.stop_price))
 def test_target_stop_and_reference_judgement(self):
  self.assertEqual(calculate(100,120,70,30).action,"續抱／觀察")
 def test_locked_levels_can_trigger_take_profit_and_stop_loss(self):
  self.assertEqual(evaluate(100,120,110,90)[0],"停利")
  self.assertEqual(evaluate(100,80,110,90)[0],"停損")
