"""Tests for the beta-hedge contract-count formula."""
import unittest

from beta_hedge import suggest_hedge


class BetaHedgeTests(unittest.TestCase):
    def test_full_hedge_to_zero_suggests_shorting(self) -> None:
        result = suggest_hedge(portfolio_value=2_400_000, current_beta=1.35, target_beta=0.0, index_points=21_600, contract="MTX")
        self.assertEqual(result.direction, "放空")
        self.assertAlmostEqual(result.contracts, (2_400_000 * 1.35) / (21_600 * 50), places=2)

    def test_target_above_current_suggests_going_long(self) -> None:
        result = suggest_hedge(portfolio_value=1_000_000, current_beta=0.5, target_beta=1.0, index_points=20_000, contract="TX")
        self.assertEqual(result.direction, "做多")

    def test_target_equals_current_needs_no_action(self) -> None:
        result = suggest_hedge(portfolio_value=1_000_000, current_beta=1.0, target_beta=1.0, index_points=20_000, contract="TX")
        self.assertEqual(result.direction, "不需操作")
        self.assertEqual(result.contracts, 0.0)

    def test_smaller_contract_gives_finer_grained_count(self) -> None:
        big = suggest_hedge(portfolio_value=500_000, current_beta=1.2, target_beta=0.0, index_points=20_000, contract="TX")
        small = suggest_hedge(portfolio_value=500_000, current_beta=1.2, target_beta=0.0, index_points=20_000, contract="TMF")
        self.assertGreater(small.contracts, big.contracts)

    def test_rejects_nonpositive_inputs(self) -> None:
        with self.assertRaises(ValueError):
            suggest_hedge(portfolio_value=0, current_beta=1.0, target_beta=0.0, index_points=20_000, contract="TX")
        with self.assertRaises(ValueError):
            suggest_hedge(portfolio_value=1_000_000, current_beta=1.0, target_beta=0.0, index_points=0, contract="TX")

    def test_rejects_unknown_contract(self) -> None:
        with self.assertRaises(ValueError):
            suggest_hedge(portfolio_value=1_000_000, current_beta=1.0, target_beta=0.0, index_points=20_000, contract="XL")

    def test_held_contracts_reduce_the_suggested_trade(self) -> None:
        target = suggest_hedge(portfolio_value=2_400_000, current_beta=1.35, target_beta=0.0, index_points=21_600, contract="MTX")
        with_held = suggest_hedge(portfolio_value=2_400_000, current_beta=1.35, target_beta=0.0, index_points=21_600, contract="MTX", held_contracts=target.target_contracts)
        self.assertEqual(with_held.direction, "不需操作")
        self.assertAlmostEqual(with_held.contracts, 0.0, places=2)

    def test_held_contracts_beyond_target_suggests_opposite_direction(self) -> None:
        result = suggest_hedge(portfolio_value=2_400_000, current_beta=1.35, target_beta=0.0, index_points=21_600, contract="MTX", held_contracts=-100.0)
        self.assertEqual(result.direction, "做多")

    def test_default_held_contracts_matches_prior_behaviour(self) -> None:
        result = suggest_hedge(portfolio_value=2_400_000, current_beta=1.35, target_beta=0.0, index_points=21_600, contract="MTX")
        self.assertEqual(result.held_contracts, 0.0)
        self.assertEqual(result.contracts, abs(result.target_contracts))


if __name__ == "__main__":
    unittest.main()
