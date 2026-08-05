import unittest
from multi_layer_risk import LayeredInputs, evaluate
class MultiLayerRiskTests(unittest.TestCase):
 def base(self,**kw):
  values=dict(price=100,reference_price=90,base_target=120,base_stop=85); values.update(kw); return LayeredInputs(**values)
 def test_atr_or_support_creates_tighter_stop(self):
  d=evaluate(self.base(price=89,atr_stop=90,support=88)); self.assertEqual(d.action,"停損"); self.assertEqual(d.effective_stop,90)
 def test_event_risk_has_priority(self): self.assertEqual(evaluate(self.base(price=80,event_risk=90)).action,"事件風險減碼")
 def test_trailing_profit_and_portfolio_limits(self):
  self.assertEqual(evaluate(self.base(price=110,peak_price=125)).action,"移動停利")
  self.assertEqual(evaluate(self.base(portfolio_weight_pct=25)).action,"組合減碼")
 def test_weak_technical_confirmation_reduces_only_after_hard_stops(self):
  self.assertEqual(evaluate(self.base(technical_score=30)).action,"技術轉弱減碼")
  self.assertEqual(evaluate(self.base(price=80,technical_score=30)).action,"停損")
 def test_breaking_the_effective_stop_while_still_above_reference_price_is_take_profit_not_stop_loss(self):
  """Real bug found via a live screenshot: a stock up 100%+ from its
  reference price, but currently below its own 20-day moving average
  (one of the effective-stop candidates), was labelled "停損" (stop-LOSS)
  even though the position has a large unrealized gain -- Taiwan
  convention calls exiting a weakening-but-still-profitable position
  "停利" (take profit), never "停損"."""
  d=evaluate(self.base(price=95,reference_price=42,moving_average=102.5,base_stop=75.7))
  self.assertEqual(d.action,"停利（轉弱）")
  self.assertEqual(d.effective_stop,102.5)
 def test_breaking_the_effective_stop_exactly_at_reference_price_is_still_take_profit_not_a_loss(self):
  """Breakeven is not a loss either."""
  d=evaluate(self.base(price=90,reference_price=90,moving_average=95))
  self.assertEqual(d.action,"停利（轉弱）")
 def test_breaking_the_effective_stop_below_reference_price_is_still_a_real_stop_loss(self):
  d=evaluate(self.base(price=85,reference_price=90,moving_average=95))
  self.assertEqual(d.action,"停損")
