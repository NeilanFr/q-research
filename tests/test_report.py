"""Synthetic checks for misleading report metrics and mismatched comparisons."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from quantlab.core import simulate
from quantlab.report import _block_ci, _metrics, build_report


def report_fixture():
    cfg = json.loads((Path(__file__).resolve().parents[1] / "config" / "first_study.json").read_text())
    cfg.update(bootstrap_samples=100, models=["equal_weight", "spy_buy_hold", "reversal", "vol_scaled_reversal"])
    dates = pd.bdate_range("2020-01-01", periods=82)
    decisions = dates[:-2].rename("decision_date")
    timing = pd.DataFrame({"entry_date": dates[1:-1], "exit_date": dates[2:]}, index=decisions)
    t = np.arange(len(decisions))
    returns = pd.DataFrame({"A": 0.002 + 0.02 * np.sin(t / 4),
                            "B": -0.001 + 0.01 * np.cos(t / 5)}, index=decisions)
    models = {}
    for name, fraction in [("equal_weight", 0.5), ("spy_buy_hold", 0.5),
                           ("reversal", 0.6), ("vol_scaled_reversal", 0.8)]:
        weights = pd.DataFrame({"A": fraction, "B": 1 - fraction}, index=decisions)
        models[name] = simulate(weights, returns, timing, cfg["cost_bps"])
        models[name]["stress"] = simulate(weights, returns, timing, cfg["stress_cost_bps"])
        models[name]["ic"] = pd.Series(np.nan if name.endswith("weight") or name.endswith("hold")
                                        else np.sin(t / 3), index=decisions)
    return cfg, {"validation": models}


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_root = Path(__file__).resolve().parents[1] / ".cache" / "tests"
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    def test_first_day_loss_counts_as_drawdown(self):
        result = _metrics(pd.Series([-0.20, 0.25, -0.10]))
        self.assertAlmostEqual(result["max_drawdown"], -0.20)
        self.assertAlmostEqual(result["total_return"], -0.10)

    def test_flat_and_single_day_returns_do_not_invent_a_sharpe(self):
        self.assertTrue(np.isnan(_metrics(pd.Series([0.0, 0.0]))["sharpe_zero_rf"]))
        self.assertTrue(np.isnan(_metrics(pd.Series([0.1]))["sharpe_zero_rf"]))

    def test_invalid_return_inputs_are_rejected(self):
        for returns in ([], [np.nan], [np.inf], [-1.0], [-1.1]):
            with self.subTest(returns=returns), self.assertRaises(ValueError):
                _metrics(pd.Series(returns, dtype=float))

    def test_block_uncertainty_retains_serial_dependence(self):
        values = np.tile(np.repeat([-0.01, 0.01], 20), 10)
        cfg = {"bootstrap_samples": 2000, "bootstrap_block": 20, "seed": 20260919}
        lo, hi = _block_ci(values, cfg)
        iid_lo, iid_hi = _block_ci(values, cfg | {"bootstrap_block": 1})
        self.assertGreater(hi - lo, 2 * (iid_hi - iid_lo))
        self.assertEqual((lo, hi), _block_ci(values, cfg))

    def test_constant_paired_advantage_interval_has_correct_units(self):
        cfg = {"bootstrap_samples": 50, "bootstrap_block": 20, "seed": 1}
        np.testing.assert_allclose(_block_ci(np.full(80, 0.001), cfg, 252), [0.252, 0.252])
        self.assertTrue(all(np.isnan(v) for v in _block_ci([np.nan, np.nan], cfg)))

    def test_report_artifacts_and_accounting_reconcile(self):
        cfg, results = report_fixture()
        manifest = {"id": "synthetic-test", "snapshot_id": "synthetic", "source_sha256": "synthetic",
                    "created_at_utc": "2026-09-19T00:00:00Z"}
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            path = Path(folder)
            summary = build_report(path, manifest, results, cfg).set_index("model")
            for filename in ["summary.csv", "yearly.csv", "sector_contributions.csv", "failures.csv", "report.md", "equity.png"]:
                self.assertGreater((path / filename).stat().st_size, 0)
            self.assertAlmostEqual(summary.loc["equal_weight", "active_equal_weight_annual"], 0)
            self.assertAlmostEqual(summary.loc["equal_weight", "active_equal_weight_ci_low"], 0)
            self.assertEqual(summary.loc["equal_weight", "ic_dates"], 0)
            self.assertTrue(np.isnan(summary.loc["equal_weight", "ic_ci_high"]))
            candidate = results["validation"]["vol_scaled_reversal"]["daily"]
            comparator = results["validation"]["reversal"]["daily"]
            self.assertAlmostEqual(summary.loc["vol_scaled_reversal", "active_comparator_annual"],
                                   (candidate.net_return - comparator.net_return).mean() * 252)
            self.assertGreater(summary.loc["vol_scaled_reversal", "cagr"],
                               summary.loc["vol_scaled_reversal", "stress_cagr"])
            attribution = pd.read_csv(path / "sector_contributions.csv")
            for model, group in attribution.groupby("model"):
                self.assertAlmostEqual(group.net_pnl_per_initial_dollar.sum(), summary.loc[model, "total_return"])
            text = (path / "report.md").read_text(encoding="utf-8")
            self.assertIn("without multiplicity correction", text)
            self.assertIn("holdout", text.lower())
            self.assertIn("zero risk-free", text)

    def test_missing_comparison_date_cannot_silently_improve_result(self):
        cfg, results = report_fixture()
        bad = copy.deepcopy(results)
        bad["validation"]["reversal"]["daily"] = bad["validation"]["reversal"]["daily"].iloc[1:]
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder, self.assertRaisesRegex(ValueError, "matched"):
            build_report(Path(folder), {"id": "bad"}, bad, cfg)

    def test_wrong_outcome_date_and_bad_attribution_are_rejected(self):
        cfg, results = report_fixture()
        for error in ("date", "attribution"):
            bad = copy.deepcopy(results)
            if error == "date":
                bad["validation"]["reversal"]["daily"].loc[:, "exit_date"] += pd.Timedelta(days=1)
            else:
                bad["validation"]["reversal"]["contributions"].loc[0, "pnl_per_initial_dollar"] += 0.1
            with self.subTest(error=error), tempfile.TemporaryDirectory(dir=self.temp_root) as folder, self.assertRaises(ValueError):
                build_report(Path(folder), {"id": "bad"}, bad, cfg)


if __name__ == "__main__":
    unittest.main()
