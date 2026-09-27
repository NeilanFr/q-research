import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from quantlab.rigor.governance import fingerprint
from quantlab.rigor.shadow import issue, prospective_gate, record_outcome, verify_candidate
from quantlab.data import calendar


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.definition = {"source_hash": "source", "config_hash": "config", "model_artifact": "model",
                           "universe": ["AAA"], "selection_rules": {"anchor_session": "2026-09-28", "cadence": 5}, "feature_definitions": [],
                           "parameters": {}, "execution_assumptions": {}}
        candidate = {**self.definition, "candidate_id": fingerprint(self.definition), "frozen_at": "2026-09-25T22:00:00Z"}
        self.path = self.root/"candidate.json"
        self.path.write_text(json.dumps(candidate))
        self.forecast = {"feature_session": "2026-09-25", "entry_at": "2026-09-28T13:30:00Z", "targets": {"AAA": .4},
                         "availability": [{"source_timestamp": "2026-09-25T20:00:00Z", "observed_at": "2026-09-25T21:00:00Z",
                                           "known_at": "2026-09-25T21:00:00Z", "calculated_at": "2026-09-25T22:01:00Z",
                                           "earliest_execution_at": "2026-09-28T13:30:00Z"}]}

    def tearDown(self):
        self.temp.cleanup()

    def issue(self, now="2026-09-25T22:02:00Z"):
        return issue(self.root, self.path, self.forecast, self.definition, now)

    def test_duplicate_late_and_modified_candidates_fail(self):
        self.issue()
        with self.assertRaises(FileExistsError):
            self.issue()
        with self.assertRaisesRegex(ValueError, "Late"):
            self.issue("2026-09-28T13:31:00Z")
        candidate = json.loads(self.path.read_text())
        candidate["parameters"] = {"tuned": True}
        self.path.write_text(json.dumps(candidate))
        with self.assertRaisesRegex(ValueError, "modified"):
            verify_candidate(self.path)

    def test_unseen_dependency_and_future_observation_fail(self):
        changed = {**self.definition, "source_hash": "changed"}
        with self.assertRaisesRegex(ValueError, "dependency"):
            issue(self.root, self.path, self.forecast, changed, "2026-09-25T22:02:00Z")
        self.forecast["availability"][0]["observed_at"] = "2026-09-29T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "observed_at"):
            self.issue()

    def test_outcomes_are_first_observed_and_cannot_claim_broker_fills(self):
        key = self.issue()
        outcome = {"fill_at": "2026-09-28T13:30:00Z", "mark_at": "2026-09-28T20:00:00Z",
                   "observed_at": "2026-09-28T21:00:00Z", "mode": "SHADOW_ONLY", "intended_orders": [],
                   "simulated_fills": [], "slippage": 0, "pnl": 0, "drawdown": 0, "attribution": {}, "snapshot_sha256": "data"}
        with self.assertRaisesRegex(ValueError, "future"):
            record_outcome(self.root, key, outcome, "2026-09-28T14:00:00Z")
        with self.assertRaisesRegex(ValueError, "broker"):
            record_outcome(self.root, key, {**outcome, "actual_paper_fill": {}}, "2026-09-28T22:00:00Z")
        record_outcome(self.root, key, outcome, "2026-09-28T22:00:00Z")
        with self.assertRaises(FileExistsError):
            record_outcome(self.root, key, outcome, "2026-09-28T22:00:00Z")

    def test_off_cadence_cannot_issue(self):
        self.forecast["feature_session"] = "2026-09-28"
        self.forecast["entry_at"] = "2026-09-29T13:30:00Z"
        with self.assertRaisesRegex(ValueError, "cadence"):
            self.issue("2026-09-28T22:02:00Z")

    def test_observed_prospective_gate_requires_minimum_history(self):
        rules = json.loads(Path("config/rigorous_v2.json").read_text())["prospective"]
        cal = calendar("2025-01-01", "2025-12-31")
        idx = cal.sessions_in_range("2025-01-01", "2025-12-31")[:126]
        daily = pd.DataFrame({"net_return": .001, "observed_at": cal.schedule.loc[idx, "close"]+pd.Timedelta(hours=1),
                              "rebalance": np.arange(len(idx)) % 5 == 0, "closed_lots": 1}, index=idx)
        ref = daily.copy()
        ref["net_return"] = 0
        self.assertTrue(prospective_gate(daily, ref, rules, "2026-01-01T00:00Z")["passed"])
        self.assertFalse(prospective_gate(daily.iloc[:20], ref.iloc[:20], rules, "2026-01-01T00:00Z")["passed"])
        with self.assertRaisesRegex(ValueError, "missing"):
            prospective_gate(daily.drop(idx[10]), ref.drop(idx[10]), rules, "2026-01-01T00:00Z")


if __name__ == "__main__":
    unittest.main()
