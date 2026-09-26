"""The cost-falsification follow-up must really retain holdings between trades."""
import unittest
import numpy as np
import pandas as pd
from quantlab.core import simulate


class CadenceTests(unittest.TestCase):
    def test_scheduled_rebalance_retains_drift_between_decisions(self):
        dates = pd.bdate_range("2020-01-01", periods=8)
        index = dates[:6]
        timing = pd.DataFrame({"entry_date": dates[1:7], "exit_date": dates[2:8]}, index=index)
        weights = pd.DataFrame([[.5, .5]] + [[0., 1.]] * 5, index=index, columns=["A", "B"])
        returns = pd.DataFrame([[.1, 0.]] * 6, index=index, columns=weights.columns)
        out = simulate(weights, returns, timing, 0, liquidate=False, rebalance_every=5)
        # First five intervals retain original shares; only the sixth rotates.
        self.assertAlmostEqual(out["daily"].nav.iloc[-1], .5 * 1.1 ** 5 + .5)
        middle = out["trades"][out["trades"].decision_date.isin(index[1:5])]
        self.assertTrue(np.allclose(middle.dollars_per_initial_dollar, 0))
        self.assertAlmostEqual(out["weights"].iloc[1, 0], .55 / 1.05)
        self.assertEqual(out["weights"].iloc[-1, 0], 0)
        for bad in [0, -1, 1.5, True]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                simulate(weights, returns, timing, 0, rebalance_every=bad)
