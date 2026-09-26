"""Prospective timing and immutable predictions, using an isolated temp ledger."""

from contextlib import ExitStack, closing
from copy import deepcopy
from itertools import chain, repeat
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from quantlab import experiment, forward


class ForwardTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / ".cache/tests"
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = TemporaryDirectory(dir=test_root)
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(test_root.resolve()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        self.cfg = {
            "symbols": ["AAA", "BBB", "CCC"], "benchmark": "SPY",
            "data_start": "2005-01-01", "discovery_start": "2006-01-01",
            "discovery_end": "2017-12-31", "validation_start": "2018-01-01",
            "validation_end": "2022-12-31", "holdout_start": "2023-01-01",
            "horizon_sessions": 1, "momentum_sessions": 3,
            "reversal_sessions": 2, "volatility_sessions": 3,
            "tilt": 0.6, "max_weight": 0.6, "cost_bps": 5,
            "stress_cost_bps": 10, "bootstrap_samples": 100, "bootstrap_block": 5,
            "models": ["equal_weight", "spy_buy_hold", "momentum"],
            "origins": {"equal_weight": "systematic_baseline", "spy_buy_hold": "systematic_baseline", "momentum": "systematic_baseline"},
            "comparators": {},
        }
        protocol = {"historical_holdout_start": "2023-01-01",
                    "competition_first_session": "2026-09-21",
                    "competition_end_assumption": "2026-10-20"}
        self.policy = {"name": "test_v1", "research_config": "config/research.json",
                       "active_model": "equal_weight", "reason": "Test baseline",
                       "execution_enabled": False, "gross_exposure": 1,
                       "notional_capital": 1_000_000}
        self.policy_path = self.root / "config/policy.json"
        (self.root / "config/research.json").write_text(json.dumps(self.cfg), encoding="utf-8")
        (self.root / "config/protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
        self.policy_path.write_text(json.dumps(self.policy), encoding="utf-8")
        dates = pd.bdate_range(end="2026-09-18", periods=15)
        self.bars = pd.DataFrame([
            {"date": date, "symbol": symbol,
             "close": 100 + j * 10 + np.sin(i * (j + 1)) + i * 0.2,
             "adj_close": 100 + j * 10 + np.sin(i * (j + 1)) + i * 0.2,
             "adj_open": 100 + j * 10 + np.cos(i * (j + 1)) + i * 0.2}
            for i, date in enumerate(dates)
            for j, symbol in enumerate(["AAA", "BBB", "CCC", "SPY"])
        ])
        self.manifest = {"id": "synthetic", "created_at_utc": "2026-09-19T11:00:00Z",
                         "raw_sources": [{"fetched_at_utc": "2026-09-19T10:00:00Z"}]}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(forward, "ROOT", self.root))
        self.stack.enter_context(patch.object(experiment, "ROOT", self.root))
        def test_connection():
            connection = experiment.connect_db()
            self.addCleanup(connection.close)
            return connection
        self.stack.enter_context(patch.object(forward, "connect_db", side_effect=test_connection))
        self.stack.enter_context(patch.object(forward, "source_archive", return_value="a" * 64))
        self.stack.enter_context(patch.object(forward, "load_snapshot", side_effect=lambda _: (self.bars, self.manifest, self.root)))
        self.clock = self.stack.enter_context(patch.object(forward, "now_utc", return_value="2026-09-19T12:00:00Z"))

    def test_friday_features_predict_monday_open_and_tuesday_open_exit(self):
        times = forward.forecast_times("2026-09-18", "2026-09-19T12:00:00Z")
        self.assertEqual(times["feature_available_at"], "2026-09-18T21:00:00+00:00")
        self.assertEqual(times["entry_at"], "2026-09-21T13:30:00+00:00")
        self.assertEqual(times["exit_at"], "2026-09-22T13:30:00+00:00")

    def test_stale_incomplete_naive_and_late_issues_are_rejected(self):
        cases = [("2026-09-17", "2026-09-19T12:00:00Z"),
                 ("2026-09-18", "2026-09-18T20:59:59Z"),
                 ("2026-09-18", "2026-09-19T12:00:00"),
                 ("2026-09-18", "2026-09-21T13:28:00Z"),
                 ("2026-09-18", "2026-09-21T13:31:00Z"),
                 ("2026-09-18", "2026-09-21T21:00:00Z")]
        for date, issued in cases:
            with self.subTest(date=date, issued=issued), self.assertRaises(ValueError):
                forward.forecast_times(date, issued)

    def test_locked_historical_cutoff_cannot_be_moved_by_experiment_config(self):
        experiment.validate_config(self.cfg)
        for changes in ({"validation_end": "2023-01-01", "holdout_start": "2024-01-01"},
                        {"validation_end": "2026-09-18", "holdout_start": "2027-01-01"},
                        {"discovery_end": "2023-01-01", "validation_start": "2024-01-01",
                         "validation_end": "2025-01-01", "holdout_start": "2026-01-01"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                experiment.validate_config(self.cfg | changes)

    def test_duplicate_policy_and_session_cannot_replace_existing_predictions(self):
        saved = forward.predict(self.policy_path)
        original = (saved / "forecast.json").read_bytes()
        self.bars.loc[self.bars.symbol == "AAA", "adj_close"] *= 1.5
        self.manifest["id"] = "revised"
        with self.assertRaisesRegex(ValueError, "already frozen"):
            forward.predict(self.policy_path)
        self.assertEqual((saved / "forecast.json").read_bytes(), original)
        with closing(sqlite3.connect(self.root / "state/research.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM forecast_batches").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM submitted_orders").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM fills").fetchone()[0], 0)

    def test_computation_crossing_entry_time_cannot_be_backdated_as_forward(self):
        self.clock.side_effect = chain(["2026-09-21T13:27:00Z"], repeat("2026-09-21T13:31:00Z"))
        with self.assertRaises(ValueError):
            forward.predict(self.policy_path)
        ledger = self.root / "state/research.sqlite"
        if ledger.exists():
            with closing(sqlite3.connect(ledger)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM forecast_batches").fetchone()[0], 0)

    def test_data_vintage_must_precede_issuance_and_follow_bar_availability(self):
        original = deepcopy(self.manifest)
        for changes in ({"created_at_utc": "2026-09-20T12:00:00Z"},
                        {"raw_sources": [{"fetched_at_utc": "2026-09-18T20:30:00Z"}]}):
            self.manifest = original | changes
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                forward.predict(self.policy_path)

    def test_evaluation_rejects_forecast_file_that_disagrees_with_frozen_ledger(self):
        saved = forward.predict(self.policy_path)
        path = saved / "forecast.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        # Change to a different valid, fully invested portfolio: simple bounds
        # checks cannot detect replacement of historical predictions.
        equal = [r for r in original["position_intents"] if r["model"] == "equal_weight"]
        for row, weight in zip(equal, [0.6, 0.3, 0.1]):
            row["target_weight"] = weight
        path.write_text(json.dumps(original), encoding="utf-8")
        last = self.bars[self.bars.date == self.bars.date.max()].copy()
        monday, tuesday = last.copy(), last.copy()
        monday["date"], tuesday["date"] = pd.Timestamp("2026-09-21"), pd.Timestamp("2026-09-22")
        tuesday.loc[tuesday.symbol == "AAA", "adj_open"] *= 1.1
        self.bars = pd.concat([self.bars, monday, tuesday], ignore_index=True)
        self.clock.side_effect = None
        self.clock.return_value = "2026-09-23T12:00:00Z"
        with self.assertRaisesRegex(ValueError, "integrity|mismatch|modified|frozen|hash"):
            forward.evaluate()

    def test_mature_shadow_return_and_first_observed_outcome_remain_frozen(self):
        saved = forward.predict(self.policy_path)
        outcome_path = saved / "outcome.json"
        pending_output = forward.evaluate()
        pending = json.loads((pending_output / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["pending_batches"], 1)
        self.assertTrue(pd.read_csv(pending_output / "summary.csv").empty)
        self.assertFalse(outcome_path.exists())

        # Known entry/exit opens make the baseline outcome independently
        # calculable: equal-weight return is (+10% - 10% + 6%) / 3 = +2%.
        last = self.bars[self.bars.date == self.bars.date.max()].copy()
        monday, tuesday = last.copy(), last.copy()
        monday["date"], tuesday["date"] = pd.Timestamp("2026-09-21"), pd.Timestamp("2026-09-22")
        monday["adj_open"] = 100.0
        tuesday["adj_open"] = tuesday.symbol.map({"AAA": 110.0, "BBB": 90.0, "CCC": 106.0, "SPY": 102.0})
        self.bars = pd.concat([self.bars, monday, tuesday], ignore_index=True)
        self.clock.return_value = "2026-09-23T12:00:00Z"
        self.manifest = {"id": "mature", "created_at_utc": "2026-09-23T11:00:00Z",
                         "raw_sources": [{"fetched_at_utc": "2026-09-23T10:00:00Z"}]}
        mature_output = forward.evaluate()
        summary = pd.read_csv(mature_output / "summary.csv").set_index("model")
        expected_net = 1.02 / 1.0005 - 1  # Initial 5bps fee; ongoing portfolio has no exit fee.
        self.assertAlmostEqual(summary.loc["equal_weight", "net_shadow_return"], expected_net, places=12)
        self.assertTrue((summary["observed_sessions"] == 1).all())
        first_outcome = outcome_path.read_bytes()
        outcome = json.loads(first_outcome)
        self.assertEqual(outcome["snapshot"], "mature")
        np.testing.assert_allclose([outcome["returns"][s] for s in ["AAA", "BBB", "CCC"]], [0.1, -0.1, 0.06])

        # A provider's later revision cannot rewrite the first observed result.
        self.bars.loc[(self.bars.date == pd.Timestamp("2026-09-22")) & (self.bars.symbol == "AAA"), "adj_open"] = 160.0
        self.manifest = {"id": "revised_mature", "created_at_utc": "2026-09-24T11:00:00Z",
                         "raw_sources": [{"fetched_at_utc": "2026-09-24T10:00:00Z"}]}
        self.clock.return_value = "2026-09-24T12:00:00Z"
        repeated_output = forward.evaluate()
        repeated = pd.read_csv(repeated_output / "summary.csv").set_index("model")
        self.assertEqual(outcome_path.read_bytes(), first_outcome)
        pd.testing.assert_frame_equal(summary, repeated)
        status = json.loads((repeated_output / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["pending_batches"], 0)
        self.assertEqual(status["submitted_orders"], 0)
        self.assertEqual(status["fills"], 0)
        with closing(sqlite3.connect(self.root / "state/research.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outcome_integrity").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM submitted_orders").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM fills").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
