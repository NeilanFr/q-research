"""Adversarial V2 tests: intentional leaks must be caught by the same assertions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from quantlab.data import calendar
from quantlab.rigor.execution import decision_times, intended_order, simulate
from quantlab.rigor.governance import (append_event, asof_records, freeze_candidate,
                                     promotion_allowed, read_ledger, require_available)
from quantlab.rigor.indicators import display_metadata, rsi, technical
from quantlab.rigor.strategies import make_panel, score_fold, weights_for
from quantlab.rigor.validation import assert_invariant, block_inference, fit_ridge, predict, training_mask


def fixture():
    cfg = json.loads(Path("config/rigorous_v2.json").read_text())
    idx = calendar("2013-01-01", "2016-12-31").sessions_in_range("2013-01-01", "2016-12-31")
    rng = np.random.default_rng(421)
    rows = []
    for symbol in ("AAA", "BBB", "CCC", "SPY"):
        cl = 100*np.exp(np.cumsum(rng.normal(.0004, .016, len(idx))))
        op = cl*np.exp(rng.normal(0, .003, len(idx)))
        rows.extend({"date": date, "symbol": symbol, "open": o, "adj_open": o,
                     "close": c, "adj_close": c, "high": max(o, c)*1.02, "low": min(o, c)*.98,
                     "volume": 1e8} for date, o, c in zip(idx, op, cl))
    return pd.DataFrame(rows), cfg


class IndicatorTests(unittest.TestCase):
    def test_wilder_seed_and_degenerate_series(self):
        close = pd.DataFrame({"up": np.arange(30)+1., "down": 40.-np.arange(30), "flat": np.ones(30)})
        v = rsi(close, 7)
        self.assertTrue(v.iloc[:7].isna().all().all())
        self.assertTrue((v.iloc[7:].up == 100).all())
        self.assertTrue((v.iloc[7:].down == 0).all())
        self.assertTrue((v.iloc[7:].flat == 50).all())
        c = pd.DataFrame({"p": [10., 11, 9, 12, 10, 13, 12, 14, 13]})
        self.assertAlmostEqual(rsi(c, 7).iloc[7, 0], 100-100/(1+9/5))

    def test_chart_offsets_are_display_only(self):
        idx = pd.bdate_range("2015-01-01", periods=100)
        c = pd.DataFrame({"A": np.arange(100)+100.}, index=idx)
        ta = technical(c, c+1, c-1)
        self.assertAlmostEqual(ta["tenkan"].iloc[70, 0], 166)
        self.assertAlmostEqual(ta["kijun"].iloc[70, 0], 157.5)
        self.assertAlmostEqual(ta["senkou_a"].iloc[70, 0], 161.75)
        self.assertAlmostEqual(ta["senkou_b"].iloc[70, 0], 144.5)
        self.assertAlmostEqual(ta["chikou_comparison"].iloc[70, 0], 170/144-1)
        meta = display_metadata(idx, 70)
        self.assertEqual(meta["calculated_at"], str(idx[70]))
        self.assertEqual(meta["chikou_displayed_at"], str(idx[44]))

    def test_all_technical_features_future_mutation_and_deletion(self):
        idx = pd.bdate_range("2015-01-01", periods=330)
        c = pd.DataFrame(np.random.default_rng(4).lognormal(4, .1, (330, 3)), index=idx)
        cutoff = 260
        original = technical(c, c+1, c-1)
        changed = c.copy()
        changed.iloc[cutoff+1:] *= 100
        for variant in (changed, c.iloc[:cutoff+1]):
            other = technical(variant, variant+1, variant-1)
            for name in original:
                with self.subTest(name=name, length=len(variant)):
                    assert_invariant(original[name].iloc[:cutoff+1], other[name].iloc[:cutoff+1])

    def test_detects_deliberately_backward_senkou_and_chikou(self):
        c = pd.DataFrame({"A": np.arange(180)+100.})
        changed = c.copy()
        changed.iloc[121:] *= 10
        for key in ("senkou_a", "senkou_b"):
            before = technical(c, c+1, c-1)[key].shift(-26)
            after = technical(changed, changed+1, changed-1)[key].shift(-26)
            with self.assertRaises(AssertionError):
                assert_invariant(before.iloc[:121], after.iloc[:121])
        with self.assertRaises(AssertionError):
            assert_invariant(c.shift(-26).iloc[:121], changed.shift(-26).iloc[:121])


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.cfg = fixture()
        cls.panel = make_panel(cls.bars, ["AAA", "BBB", "CCC"], cls.cfg)
        cls.champion = {"training": {"ridge": [.001]+[.002]*len(cls.panel["feature_names"])}}

    def test_features_predictions_targets_orders_future_invariant(self):
        p, cfg = self.panel, self.cfg
        cutoff = pd.Timestamp("2016-06-01")
        scores, models = score_fold(p, cfg, "2016-01-01", self.champion)
        changed = self.bars.copy()
        future = changed.date > cutoff
        changed.loc[future, ["open", "adj_open", "close", "adj_close", "high", "low", "volume"]] *= 123
        for bars in (changed, self.bars[self.bars.date <= cutoff]):
            q = make_panel(bars, ["AAA", "BBB", "CCC"], cfg)
            other, fitted = score_fold(q, cfg, "2016-01-01", self.champion)
            mask = p["closes"].index <= cutoff
            assert_invariant(p["x"][mask], q["x"][:mask.sum()])
            assert_invariant(p["eligible"].loc[:cutoff], q["eligible"].loc[:cutoff])
            for block in models:
                for key in ("mean", "scale", "lower", "upper", "coef"):
                    assert_invariant(models[block][key], fitted[block][key])
            for name in scores:
                with self.subTest(name=name, rows=len(bars)):
                    assert_invariant(scores[name].loc[:cutoff], other[name].loc[:cutoff])
                    target = weights_for(p, scores, name, cfg)
                    changed_target = weights_for(q, other, name, cfg)
                    assert_invariant(target.loc[:cutoff], changed_target.loc[:cutoff])
                    args = [np.zeros(3), 1., p["closes"].loc[cutoff], p["adv"].loc[cutoff], 1e6]
                    assert_invariant(intended_order(target.loc[cutoff], *args), intended_order(changed_target.loc[cutoff], *args))

    def test_preprocessing_changes_in_validation_cannot_change_fit(self):
        p = self.panel
        x = p["x"].copy()
        kwargs = dict(start="2013-04-01", evaluation_start="2016-01-01")
        fit = fit_ridge(x, p["opens"], p["eligible"], **kwargs)
        future = p["opens"].index >= "2016-01-01"
        x[future] = 1e9
        opens = p["opens"].copy()
        opens.loc[future] *= 900
        changed = fit_ridge(x, opens, p["eligible"], **kwargs)
        for name in ("coef", "mean", "scale", "lower", "upper"):
            assert_invariant(fit[name], changed[name])
        with self.assertRaises(AssertionError):
            assert_invariant((p["x"]-p["x"].mean(axis=(0, 1)))[:700], (x-x.mean(axis=(0, 1)))[:700])

    def test_future_return_feature_is_detected(self):
        c = self.panel["closes"].copy()
        changed = c.copy()
        changed.iloc[701:] *= 10
        with self.assertRaises(AssertionError):
            assert_invariant((c.shift(-10)/c-1).iloc[:701], (changed.shift(-10)/changed-1).iloc[:701])

    def test_purge_and_embargo_actual_label_exit(self):
        idx = self.panel["opens"].index
        mask = training_mask(idx, "2013-04-01", "2016-01-01", 10, 10)
        boundary = idx.searchsorted(pd.Timestamp("2016-01-01"))
        self.assertLess(np.flatnonzero(mask)[-1]+11, boundary-10)

    def test_fold_fit_cannot_see_later_fold(self):
        p = self.panel
        m = fit_ridge(p["x"], p["opens"], p["eligible"], "2013-04-01", "2015-01-01")
        self.assertLess(m["last_label_exit"], "2015-01-01")
        self.assertEqual(predict(p["x"], m).shape, p["opens"].shape)


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bars, cls.cfg = fixture()
        cls.panel = make_panel(bars, ["AAA", "BBB", "CCC"], cls.cfg)
        cls.targets = cls.panel["closes"]*0+.3

    def test_accounting_costs_lots_and_no_same_close(self):
        p = self.panel
        sim = simulate(p, self.targets, "2016-01-01", "2016-06-30", self.cfg["execution"])
        stress = simulate(p, self.targets, "2016-01-01", "2016-06-30", self.cfg["execution"], cost_multiple=3)
        self.assertAlmostEqual(sim["contributions"].to_numpy().sum(), sim["daily"].nav.iloc[-1]-1)
        self.assertAlmostEqual(sim["roundtrips"].pnl.sum(), sim["daily"].nav.iloc[-1]-1)
        self.assertLess(stress["daily"].nav.iloc[-1], sim["daily"].nav.iloc[-1])
        self.assertTrue((sim["trades"].decision_date < sim["trades"].date).all())
        self.assertTrue((sim["daily"].gross_exposure <= 1+1e-9).all())

    def test_known_future_open_does_not_size_order(self):
        p = self.panel
        day = p["opens"].index[p["opens"].index.searchsorted(pd.Timestamp("2016-01-01"))]
        before = simulate(p, self.targets, "2016-01-01", "2016-01-15", self.cfg["execution"])
        changed = {**p, "opens": p["opens"].copy()}
        changed["opens"].loc[day] *= .9
        after = simulate(changed, self.targets, "2016-01-01", "2016-01-15", self.cfg["execution"])
        # Cheaper execution cannot buy extra units beyond the frozen order.
        assert_invariant(before["trades"].query("date == @day").units, after["trades"].query("date == @day").units)

    def test_same_bar_injected_timing_fails(self):
        p = copy.deepcopy(self.panel)
        p["clock"]["decision_at"] = p["clock"].earliest_execution_at
        with self.assertRaisesRegex(ValueError, "Same-bar"):
            simulate(p, self.targets, "2016-01-01", "2016-02-01", self.cfg["execution"])

    def test_missing_data_and_holidays_fail_closed(self):
        p = {**self.panel, "opens": self.panel["opens"].copy()}
        p["opens"].loc["2016-01-04", "AAA"] = np.nan
        with self.assertRaisesRegex(ValueError, "Missing"):
            simulate(p, self.targets, "2016-01-01", "2016-02-01", self.cfg["execution"])
        with self.assertRaisesRegex(ValueError, "sessions"):
            decision_times(pd.bdate_range("2016-01-01", "2016-02-01"))

    def test_no_trade_costs_zero_and_missed_fills_recorded(self):
        sim = simulate(self.panel, self.targets*0, "2016-01-01", "2016-02-01", self.cfg["execution"])
        self.assertTrue((sim["daily"].net_return == 0).all())
        missed = simulate(self.panel, self.targets, "2016-01-01", "2016-02-01", self.cfg["execution"], miss_every=1)
        self.assertEqual(len(missed["trades"]), 0)
        delayed = simulate(self.panel, self.targets, "2016-01-01", "2016-02-01", self.cfg["execution"], delay=1)
        self.assertEqual(delayed["daily"].gross_exposure.iloc[0], 0)


class GovernanceTests(unittest.TestCase):
    def test_membership_sectors_and_actions_future_records_do_not_backdate(self):
        for field in ("membership", "sector", "split", "dividend", "merger", "symbol_change", "spinoff"):
            rows = pd.DataFrame([{"symbol": "AAA", "effective_at": "2015-01-01T00:00Z", "known_at": "2015-01-01T00:00Z", "value": 1},
                                 {"symbol": "AAA", "effective_at": "2015-01-01T00:00Z", "known_at": "2017-01-01T00:00Z", "value": 900}])
            before = asof_records(rows.iloc[:1], "2016-01-01T00:00Z")
            after = asof_records(rows, "2016-01-01T00:00Z")
            assert_invariant(before, after)
            with self.subTest(field=field), self.assertRaises(AssertionError):
                # Intentionally broken current-constituent/sector/action projection.
                assert_invariant(before, rows.drop_duplicates("symbol", keep="last").set_index("symbol").value)

    def test_actual_observation_time_cannot_be_inferred_from_row_date(self):
        record = {"source_timestamp": "2015-01-01T21:00Z", "known_at": "2015-01-01T22:00Z",
                  "observed_at": "2026-09-21T00:00Z", "calculated_at": "2015-01-01T22:00Z",
                  "earliest_execution_at": "2015-01-02T14:30Z"}
        with self.assertRaisesRegex(ValueError, "observed_at"):
            require_available([record], "2015-01-01T22:01Z")

    def test_ledger_detects_rewrite_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"ledger.jsonl"
            append_event(path, "test", {"a": 1})
            append_event(path, "test", {"a": 2})
            self.assertEqual(len(read_ledger(path)), 2)
            path.write_text(path.read_text().replace('"a":1', '"a":9'))
            with self.assertRaisesRegex(ValueError, "broken"):
                read_ledger(path)

    def test_no_missing_gate_can_promote_or_freeze(self):
        self.assertFalse(promotion_allowed({"discovery": True, "validation": True}))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "skip"):
                freeze_candidate(Path(directory)/"candidate.json", {}, {"discovery": True})

    def test_block_bootstrap_shared_dates_and_null_not_significant(self):
        x = np.zeros((100, 3))
        out = block_inference(x, samples=100)
        np.testing.assert_equal(out["familywise_p"], 1)
        self.assertEqual(out["global_p"], 1)
        x = np.random.default_rng(3).normal(0, .01, 300)
        out = block_inference(np.column_stack([x, x]), samples=100)
        self.assertEqual(out["lower"][0], out["lower"][1])


if __name__ == "__main__":
    unittest.main()
