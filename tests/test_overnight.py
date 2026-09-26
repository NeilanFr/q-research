"""Hand-calculated overnight fees, adjustment identities, and MOC information lag."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from quantlab.overnight import _account, _break_even, decompose, run_overnight


def bars_from_prices(dates, opens, closes, symbol="SPY", factors=None):
    opens, closes = np.asarray(opens, float), np.asarray(closes, float)
    factors = np.ones(len(closes)) if factors is None else np.asarray(factors, float)
    return pd.DataFrame({"date": dates, "symbol": symbol, "open": opens, "close": closes,
                         "high": np.maximum(opens, closes) * 1.01,
                         "low": np.minimum(opens, closes) * 0.99,
                         "adj_open": opens * factors, "adj_close": closes * factors,
                         "volume": 1_000_000.0})


class OvernightTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / ".cache/tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = TemporaryDirectory(dir=root)
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(root.resolve()))
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)

    def test_raw_adjusted_decomposition_and_factor_change_identity(self):
        dates = pd.date_range("2020-01-01", periods=3)
        bars = bars_from_prices(dates, [99, 110, 55], [100, 121, 60], factors=[0.5, 0.5, 1])
        result = decompose(bars)
        self.assertAlmostEqual(result.raw_overnight.iloc[1], 0.1)
        self.assertAlmostEqual(result.raw_intraday.iloc[1], 0.1)
        self.assertAlmostEqual(result.raw_close_close.iloc[1], 0.21)
        self.assertAlmostEqual(result.adjusted_overnight.iloc[2], 55 / 60.5 - 1)
        self.assertAlmostEqual(result.adjustment_log_effect.iloc[2], np.log(2))
        self.assertLess(result.filter(regex="residual$").abs().max().max(), 1e-12)
        np.testing.assert_allclose(result.raw_intraday, result.adjusted_intraday)
        for prefix in ("raw", "adjusted"):
            np.testing.assert_allclose(
                np.log1p(result[f"{prefix}_overnight"].iloc[1:])
                + np.log1p(result[f"{prefix}_intraday"].iloc[1:]),
                np.log1p(result[f"{prefix}_close_close"].iloc[1:]), atol=1e-12)

    def test_adjusted_open_mismatch_missing_and_duplicate_prices_rejected(self):
        good = bars_from_prices(pd.date_range("2020-01-01", periods=3), [100] * 3, [102] * 3)
        bad = good.copy()
        bad.loc[1, "adj_open"] *= 1.1
        missing = good.copy()
        missing.loc[1, "open"] = np.nan
        for frame in (bad, missing, pd.concat([good, good.iloc[[1]]])):
            with self.subTest(frame=frame.shape), self.assertRaises(ValueError):
                decompose(frame)

    def test_round_trip_charges_two_legs_and_disabled_day_stays_cash(self):
        account = _account(np.array([0.1, -0.9]), np.array([1.0, 0.0]), 100)
        self.assertAlmostEqual(account["nav"][0], 1.1 * 0.99 / 1.01)
        self.assertAlmostEqual(account["turnover"][0], 2.1 / 1.01)
        self.assertAlmostEqual(account["cost_fraction"][0], 0.021 / 1.01)
        self.assertEqual(account["net_return"][1], 0)
        self.assertEqual(account["turnover"][1], 0)
        self.assertEqual(account["cost_fraction"][1], 0)
        self.assertAlmostEqual(account["gross_pnl_per_initial_dollar"].sum()
                               - account["cost_per_initial_dollar"].sum(), account["nav"][-1] - 1)

    def test_fractional_cash_control_pays_fees_only_on_its_position(self):
        account = _account(np.array([0.1]), np.array([0.5]), 100)
        # $100/201 in cash and stock each; stock grows to $110/201,
        # then exit fee is $1.1/201. Entry fee was $1/201.
        self.assertAlmostEqual(account["nav"][0], 208.9 / 201)
        self.assertAlmostEqual(account["cost_fraction"][0], 2.1 / 201)
        self.assertAlmostEqual(account["turnover"][0], 210 / 201)

    def test_full_buy_hold_pays_initial_terminal_only(self):
        r = np.array([0.1, 0.02, -0.05])
        account = _account(r, np.ones(3), 100, buy_hold=True)
        self.assertAlmostEqual(account["nav"][-1], 1.1 * 1.02 * 0.95 * 0.99 / 1.01)
        self.assertEqual(account["turnover"][1], 0)
        self.assertEqual(account["cost_fraction"][1], 0)
        self.assertGreater(account["cost_fraction"][0], 0)
        self.assertGreater(account["cost_fraction"][-1], 0)

    def test_break_even_fee_matches_hand_solved_round_trip(self):
        fee, capped = _break_even(np.array([0.02]), np.ones(1), False)
        self.assertFalse(capped)
        self.assertAlmostEqual(fee, 10000 * 0.02 / 2.02, places=6)
        self.assertEqual(_break_even(np.array([-0.02]), np.ones(1), False), (0, False))

    def fixture(self):
        dates = pd.bdate_range("2010-01-04", periods=350)
        t = np.arange(len(dates))
        prices = []
        for j, symbol in enumerate(("SPY", "QQQ")):
            closes = 100 * np.exp(0.0002 * t + 0.03 * np.sin(0.7 * t + j))
            prices.append(bars_from_prices(dates, closes * 0.999, closes, symbol))
        cfg = {"overnight_symbols": ["SPY", "QQQ"],
               "discovery_start": str(dates[250].date()), "discovery_end": str(dates[270].date()),
               "validation_start": str(dates[280].date()), "validation_end": str(dates[310].date()),
               "cost_bps": 5, "stress_cost_bps": 10, "cost_grid_bps": [0, 1, 2.5, 5, 10],
               "bootstrap_samples": 100, "bootstrap_block": 5, "seed": 12}
        return pd.concat(prices, ignore_index=True), cfg, dates

    def test_same_close_and_future_shock_cannot_change_conditional_moc_weights(self):
        bars, cfg, dates = self.fixture()
        first = run_overnight(bars, cfg, self.output / "first")
        shocked = bars.copy()
        changed = shocked.date >= dates[260]
        for field in ("open", "high", "low", "close", "adj_open", "adj_close"):
            shocked.loc[changed, field] *= 2
        second = run_overnight(shocked, cfg, self.output / "second")
        for name in first:
            with self.subTest(model=name):
                assert_frame_equal(first[name]["weights"].loc[:dates[261]], second[name]["weights"].loc[:dates[261]])
        daily = first["SPY_risk_on_overnight"]["daily"]
        self.assertEqual(daily.loc[dates[261], "entry_date"], dates[260])
        self.assertEqual(daily.loc[dates[261], "feature_date"], dates[259])
        spy = bars[bars.symbol == "SPY"].set_index("date").adj_close
        expected = float(spy.iloc[259] > spy.iloc[60:260].mean())
        self.assertEqual(daily.loc[dates[261], "exposure"], expected)

    def test_partition_boundaries_reset_cash_and_all_costs_are_reported(self):
        bars, cfg, dates = self.fixture()
        results = run_overnight(bars, cfg, self.output / "run")
        expected_dates = dates[251:271].append(dates[281:311])
        for name, result in results.items():
            with self.subTest(model=name):
                self.assertTrue(result["daily"].index.equals(expected_dates))
                for partition, rows in result["daily"].groupby("partition", sort=False):
                    self.assertEqual(rows.nav_before.iloc[0], 1)
                    self.assertTrue((rows.entry_date >= pd.Timestamp(cfg[partition + "_start"])).all())
        sensitivity = pd.read_csv(self.output / "run/cost_sensitivity.csv")
        self.assertEqual(set(sensitivity.cost_bps_per_leg), {0, 1, 2.5, 5, 10})
        self.assertEqual(len(sensitivity), len(results) * 2 * 5)

    def test_holdout_bars_or_partition_are_rejected_before_output(self):
        bars, cfg, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, "holdout"):
            run_overnight(bars, cfg | {"validation_end": "2023-01-01"}, self.output / "forbidden")
        self.assertFalse((self.output / "forbidden").exists())
        future = bars.iloc[[-1]].copy()
        future["date"] = pd.Timestamp("2023-01-03")
        with self.assertRaisesRegex(ValueError, "holdout"):
            decompose(pd.concat([bars, future]))


if __name__ == "__main__":
    unittest.main()
