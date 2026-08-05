import unittest
from pathlib import Path
from judgement_weights import default_weights, load, save
from weighted_analysis import load_weight_config

class JudgementWeightTests(unittest.TestCase):
 def test_defaults_are_all_five(self): self.assertTrue(all(x==5 for x in default_weights().values()))
 def test_saved_values_override_analysis_config(self):
  path=Path("data/test_judgement_weights.json"); weights=default_weights(); weights["technical"]=10; save(path,weights)
  self.assertEqual(load(path)["technical"],10)
 def test_invalid_range_rejected(self):
  bad=default_weights(); bad["technical"]=11
  with self.assertRaises(ValueError): save(Path("data/test_invalid_judgement_weights.json"),bad)
