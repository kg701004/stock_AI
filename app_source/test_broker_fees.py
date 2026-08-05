import unittest
from broker_fees import estimate
class BrokerFeeTests(unittest.TestCase):
 def test_kgi_public_taiwan_stock_rules(self):
  self.assertEqual(estimate(10,100,'BUY').fee,20)
  self.assertEqual(estimate(100,1000,'SELL').tax,300)
  self.assertEqual(estimate(100,1000,'SELL',is_day_trade=True).tax,150)
  self.assertEqual(estimate(100,1000,'SELL',is_etf=True).tax,100)

 def test_discount_only_applies_to_commission_not_tax(self):
  full = estimate(1000,1000,'SELL')
  discounted = estimate(1000,1000,'SELL',discount=0.6)
  self.assertEqual(discounted.fee, round(full.fee*0.6))
  self.assertEqual(discounted.tax, full.tax)  # government tax is never discounted

 def test_discount_out_of_range_is_rejected(self):
  with self.assertRaises(ValueError): estimate(100,1000,'BUY',discount=0)
  with self.assertRaises(ValueError): estimate(100,1000,'BUY',discount=1.5)

 def test_minimum_fee_floor_still_applies_under_discount(self):
  # A tiny trade's fee floor (NT$20) must not be discounted away to near-zero.
  self.assertEqual(estimate(10,100,'BUY',discount=0.1).fee, 20)
