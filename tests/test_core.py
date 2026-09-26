"""Hand-calculated accounting and temporal-invariance research checks."""

import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal, assert_index_equal

from quantlab.core import (
    centered_rank,
    make_signals,
    make_targets,
    partition_dates,
    score_weights,
    simulate,
    trading_cost,
)


def price_fixture():
    t = np.arange(20)
    returns = np.column_stack((
        0.001 + 0.012 * np.sin(t),
        0.003 * np.cos(0.7 * t),
        -0.002 + 0.010 * np.sin(0.9 * t + 1),
    ))
    return pd.DataFrame(
        np.array([100, 80, 60]) * np.cumprod(1 + returns, axis=0),
        index=pd.bdate_range("2020-01-01", periods=len(t)),
        columns=["A", "B", "C"],
    )


def portfolio_fixture(asset_returns):
    index = pd.date_range("2020-01-01", periods=len(asset_returns))
    returns = pd.DataFrame(asset_returns, index=index, columns=["A", "B"])
    weights = pd.DataFrame(0.5, index=index, columns=returns.columns)
    timing = pd.DataFrame({
        "entry_date": index + pd.Timedelta(days=1),
        "exit_date": index + pd.Timedelta(days=2),
    }, index=index)
    return weights, returns, timing


class TemporalTests(unittest.TestCase):
    cfg = {"momentum_sessions": 5, "reversal_sessions": 2, "volatility_sessions": 3}

    def test_future_price_perturbation_cannot_change_prior_signals(self):
        prices = price_fixture()
        cutoff = prices.index[11]
        original = make_signals(prices, self.cfg)
        changed = prices.copy()
        changed.loc[changed.index > cutoff] *= np.array([0.2, 5.0, 1.8])
        perturbed = make_signals(changed, self.cfg)
        for model in original:
            with self.subTest(model=model):
                assert_frame_equal(original[model].loc[:cutoff], perturbed[model].loc[:cutoff])
                self.assertGreater(len(original[model].loc[:cutoff]), 1)

    def test_full_history_and_truncated_history_give_same_current_signal(self):
        prices = price_fixture()
        cutoff = prices.index[11]
        full = make_signals(prices, self.cfg)
        truncated = make_signals(prices.loc[:cutoff], self.cfg)
        for model in full:
            with self.subTest(model=model):
                assert_frame_equal(full[model].loc[:cutoff], truncated[model])

    def test_invalid_lookbacks_cannot_introduce_forward_shifts(self):
        for key, value in (("momentum_sessions", -2), ("reversal_sessions", -1),
                           ("momentum_sessions", 0), ("reversal_sessions", 0),
                           ("volatility_sessions", 1)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                make_signals(price_fixture(), self.cfg | {key: value})

    def test_common_warmup_covers_all_configured_feature_windows(self):
        prices = price_fixture()
        signals = make_signals(prices, self.cfg | {"reversal_sessions": 8})
        for model, scores in signals.items():
            with self.subTest(model=model):
                self.assertEqual(scores.index[0], prices.index[8])
                self.assertTrue(np.isfinite(scores.to_numpy()).all())
                assert_index_equal(scores.index, signals["equal_weight"].index)

    def test_target_is_next_open_to_following_open(self):
        index = pd.date_range("2020-01-01", periods=5)
        opens = pd.DataFrame({"A": [100, 110, 121, 90, 108]}, index=index)
        targets, timing = make_targets(opens)
        np.testing.assert_allclose(targets["A"].iloc[:3], [0.1, 90 / 121 - 1, 0.2])
        self.assertTrue(targets.iloc[-2:].isna().all().all())
        self.assertEqual(timing.loc[index[0], "entry_date"], index[1])
        self.assertEqual(timing.loc[index[0], "exit_date"], index[2])
        # Same-day open is already in the past at the decision close.
        changed = opens.copy()
        changed.loc[index[0], "A"] = 10_000
        changed_targets, _ = make_targets(changed)
        self.assertEqual(targets.loc[index[0], "A"], changed_targets.loc[index[0], "A"])

    def test_partition_purges_labels_crossing_end_and_incomplete_labels(self):
        index = pd.date_range("2020-01-01", periods=7)
        _, timing = make_targets(pd.DataFrame({"A": np.arange(100, 107)}, index=index))
        selected = partition_dates(timing, "2020-01-02", "2020-01-05")
        assert_index_equal(selected, index[1:3])
        self.assertTrue((timing.loc[selected, "exit_date"] <= pd.Timestamp("2020-01-05")).all())
        all_complete = partition_dates(timing, "2020-01-01", "2030-01-01")
        assert_index_equal(all_complete, index[:5])

    def test_invalid_price_data_rejected(self):
        original = price_fixture()
        missing = original.copy()
        missing.iloc[0, 0] = np.nan
        for invalid in (original.iloc[::-1], original.iloc[[0, 0, 1, 2]],
                        missing, original * -1):
            for function in (make_targets, lambda x: make_signals(x, self.cfg)):
                with self.subTest(function=function), self.assertRaises(ValueError):
                    function(invalid)


class MappingTests(unittest.TestCase):
    def test_rank_mapping_is_identical_for_monotonic_score_representations(self):
        scores = pd.DataFrame([[1.0, 2.0, 3.0]], columns=["A", "B", "C"])
        expected = np.array([[0.4 / 3, 1 / 3, 1.6 / 3]])
        first = score_weights(scores, tilt=0.6, cap=0.6)
        transformed = score_weights(scores.pow(3) + 100, tilt=0.6, cap=0.6)
        np.testing.assert_allclose(first, expected)
        assert_frame_equal(first, transformed)
        np.testing.assert_allclose(first.sum(axis=1), 1)
        self.assertTrue((first >= 0).all().all())
        self.assertTrue((first <= 0.6).all().all())

    def test_neutral_scores_and_zero_tilt_map_to_equal_weight(self):
        scores = pd.DataFrame([[0.0, 0.0, 0.0], [1.0, -3.0, 2.0]])
        np.testing.assert_allclose(score_weights(scores.iloc[:1], 0.9, 0.7), 1 / 3)
        np.testing.assert_allclose(score_weights(scores, 0.0, 0.4), 1 / 3)

    def test_ties_do_not_create_ticker_order_edge(self):
        scores = pd.DataFrame([[1.0, 1.0, 3.0, 4.0]], columns=list("ABCD"))
        ranks = centered_rank(scores)
        np.testing.assert_allclose(ranks, [[-2 / 3, -2 / 3, 1 / 3, 1]])
        permutation = list("DCBA")
        assert_frame_equal(ranks, centered_rank(scores[permutation])[scores.columns])
        np.testing.assert_allclose(ranks.sum(axis=1), 0, atol=1e-15)

    def test_impossible_cap_and_missing_scores_rejected(self):
        scores = pd.DataFrame([[1.0, 2.0, 3.0]])
        for tilt, cap in ((0.6, 0.4), (-0.1, 1), (1.1, 1), (0.5, 0)):
            with self.subTest(tilt=tilt, cap=cap), self.assertRaises(ValueError):
                score_weights(scores, tilt, cap)
        with self.assertRaises(ValueError):
            score_weights(scores * np.nan, 0.5, 1)


class AccountingTests(unittest.TestCase):
    def test_initial_purchase_does_not_borrow_to_pay_fees(self):
        cost, delta = trading_cost(np.zeros(2), np.array([0.5, 0.5]), 0.01)
        self.assertAlmostEqual(cost, 1 / 101)
        np.testing.assert_allclose(delta, [50 / 101, 50 / 101])
        self.assertAlmostEqual(delta.sum() + cost, 1)

    def test_full_rotation_charges_both_sell_and_buy(self):
        cost, delta = trading_cost(np.array([1.0, 0.0]), np.array([0.0, 1.0]), 0.01)
        self.assertAlmostEqual(cost, 2 / 101)
        np.testing.assert_allclose(delta, [-1.0, 99 / 101])
        self.assertAlmostEqual(cost, np.abs(delta).sum() * 0.01)

    def test_drifted_weights_drive_rebalancing_turnover(self):
        w, r, timing = portfolio_fixture([[0.2, 0.0], [0.0, 0.1]])
        result = simulate(w, r, timing, 0, liquidate=False)
        daily = result["daily"]
        self.assertAlmostEqual(daily.iloc[0]["nav"], 1.1)
        self.assertAlmostEqual(daily.iloc[1]["nav"], 1.155)
        # After A rises, holdings are $0.60/$0.50. Rebalance to $0.55/$0.55.
        trades = result["trades"].query("decision_date == @w.index[1]")
        np.testing.assert_allclose(trades["dollars_per_initial_dollar"], [-0.05, 0.05])
        self.assertAlmostEqual(daily.iloc[1]["turnover"], 0.1 / 1.1)

    def test_entry_and_terminal_costs_and_attribution_reconcile_by_hand(self):
        w, r, timing = portfolio_fixture([[0.2, 0.0]])
        result = simulate(w, r, timing, 100)
        daily = result["daily"].iloc[0]
        # $1 -> $100/101 invested -> $110/101 -> $108.9/101 after selling.
        self.assertAlmostEqual(daily["nav"], 108.9 / 101)
        self.assertAlmostEqual(daily["net_return"], 7.9 / 101)
        self.assertAlmostEqual(daily["cost_fraction"], 2.1 / 101)
        self.assertAlmostEqual(daily["turnover"], 210 / 101)
        self.assertAlmostEqual(result["contributions"]["pnl_per_initial_dollar"].sum(), 10 / 101)
        self.assertAlmostEqual(result["trades"]["cost_per_initial_dollar"].sum(), 2.1 / 101)
        self.assertAlmostEqual(
            result["contributions"]["pnl_per_initial_dollar"].sum()
            - result["trades"]["cost_per_initial_dollar"].sum(),
            daily["nav"] - 1,
        )
        liquidation = result["trades"].query("kind == 'liquidation'")
        np.testing.assert_allclose(liquidation["dollars_per_initial_dollar"], [-60 / 101, -50 / 101])
        self.assertTrue((liquidation["execution_date"] == timing.iloc[0]["exit_date"]).all())

    def test_buy_hold_uses_drift_and_has_no_interim_trading(self):
        w, r, timing = portfolio_fixture([[0.2, 0.0], [0.0, 0.1]])
        result = simulate(w, r, timing, 100, buy_hold=True, liquidate=False)
        self.assertAlmostEqual(result["daily"].iloc[-1]["nav"], 1.15 / 1.01)
        self.assertAlmostEqual(result["daily"].iloc[-1]["turnover"], 0)
        np.testing.assert_allclose(result["weights"].iloc[1], [6 / 11, 5 / 11])
        interim = result["trades"].query("decision_date == @w.index[1]")
        np.testing.assert_allclose(interim["dollars_per_initial_dollar"], 0, atol=1e-14)
        np.testing.assert_allclose(interim["cost_per_initial_dollar"], 0, atol=1e-14)

    def test_multiperiod_nav_changes_equal_attribution_less_actual_trade_cost(self):
        w, r, timing = portfolio_fixture([[0.2, -0.1], [-0.15, 0.2], [0.07, -0.04]])
        w.iloc[1] = [0.2, 0.8]
        result = simulate(w, r, timing, 37)
        daily = result["daily"]
        contribution = result["contributions"].groupby("decision_date")["pnl_per_initial_dollar"].sum()
        fees = result["trades"].groupby("decision_date")["cost_per_initial_dollar"].sum()
        np.testing.assert_allclose(contribution - fees, daily["nav"] - daily["nav_before"], atol=1e-13)
        np.testing.assert_allclose(fees, daily["nav_before"] * daily["cost_fraction"], atol=1e-13)
        self.assertAlmostEqual(contribution.sum() - fees.sum(), daily.iloc[-1]["nav"] - 1)

    def test_invalid_or_misaligned_simulation_data_is_rejected(self):
        w, r, timing = portfolio_fixture([[0.1, -0.1], [0.05, 0.0]])
        cases = [(w, r[["B", "A"]], timing), (w, r, timing.iloc[::-1]),
                 (w, r.mask(r == 0.1), timing), (w * 1.1, r, timing),
                 (w, r.where(r != 0.1, -1.0), timing)]
        negative = w.copy()
        negative.iloc[0] = [-0.1, 1.1]
        cases.append((negative, r, timing))
        noncontiguous = timing.copy()
        noncontiguous.iloc[1] += pd.Timedelta(days=2)
        cases.append((w, r, noncontiguous))
        impossible = timing.copy()
        impossible.iloc[0, 0] = impossible.index[0]
        cases.append((w, r, impossible))
        for i, args in enumerate(cases):
            with self.subTest(case=i), self.assertRaises(ValueError):
                simulate(*args, 5)

    def test_duplicate_symbols_cannot_make_ambiguous_trades(self):
        w, r, timing = portfolio_fixture([[0.1, -0.1]])
        w.columns = r.columns = ["A", "A"]
        with self.assertRaises(ValueError):
            simulate(w, r, timing, 5)

    def test_nonchronological_decisions_cannot_create_misordered_history(self):
        w, r, timing = portfolio_fixture([[0.1, -0.1], [0.05, 0.0]])
        decisions = pd.DatetimeIndex(["2020-01-02", "2020-01-01"])
        w.index = r.index = timing.index = decisions
        timing["entry_date"] = pd.to_datetime(["2020-01-03", "2020-01-04"])
        timing["exit_date"] = pd.to_datetime(["2020-01-04", "2020-01-05"])
        with self.assertRaises(ValueError):
            simulate(w, r, timing, 5)


if __name__ == "__main__":
    unittest.main()
