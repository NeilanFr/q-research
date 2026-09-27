"""Read-only inventory of data provenance, prior attempts, and frozen artifacts."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3

import numpy as np

from quantlab.data import ROOT, digest, now_utc


def source_hashes():
    files = sorted([*ROOT.glob("quantlab/*.py"), *ROOT.glob("quantlab/rigor/*.py")])
    return {str(p.relative_to(ROOT)).replace("\\", "/"): digest(p.read_bytes()) for p in files}


def audit_inputs(bars, manifest, champion):
    policy = json.loads((ROOT/"config/model_v1/COMPETITION_V1.json").read_text())
    frozen = {name: digest((ROOT/"quantlab"/name).read_bytes()) == sha for name, sha in policy["source_hashes"].items()}
    artifacts = {name: digest((ROOT/policy["research_path"]/name).read_bytes()) == sha for name, sha in policy["artifacts"].items()}
    if not all(frozen.values()) or not all(artifacts.values()):
        raise ValueError("Champion source/artifact firewall breached")
    universe = json.loads((ROOT/"config/phase2_universe.json").read_text())
    names = champion["universe"]["symbols"] if isinstance(champion["universe"], dict) else champion["universe"]
    excluded = [r for r in universe["records"] if r.get("symbol") not in names]
    events = Counter()
    observation_times = []
    for source in manifest["raw_sources"]:
        payload = json.loads((ROOT/source["file"]).read_text())
        vendor_events = payload["chart"]["result"][0].get("events", {})
        events.update({k: len(v) for k, v in vendor_events.items()})
        observation_times.append(source["fetched_at_utc"])
    with sqlite3.connect(f"file:{(ROOT/'state/research.sqlite').as_posix()}?mode=ro", uri=True) as db:
        prior = dict(db.execute("SELECT status,count(*) FROM trials GROUP BY status").fetchall())
        opened = db.execute("SELECT at_utc,kind FROM events WHERE kind LIKE '%holdout%opened%'").fetchall()
    adjustment = bars.adj_close/bars.close
    mismatch = np.max(np.abs(bars.adj_open-bars.open*adjustment))
    return {"audited_at": now_utc(), "champion_source_intact": frozen, "champion_artifacts_intact": artifacts,
            "snapshot": manifest["id"], "data_hash": manifest["bars_sha256"], "historical_roster": len(universe["records"]),
            "included": len(names), "excluded": len(excluded), "excluded_fraction": len(excluded)/len(universe["records"]),
            "excluded_identities": [{"name": r["historical_name"], "reason": r["lineage_note"]} for r in excluded],
            "prior_trial_rows": prior, "holdout_opening_events": opened,
            "observed_at_min": min(observation_times), "observed_at_max": max(observation_times),
            "observed_historically": False, "point_in_time_data": False, "vendor_action_event_counts": dict(events),
            "adjusted_open_max_identity_error": float(mismatch),
            "adjustment_factor_range": [float(adjustment.min()), float(adjustment.max())],
            "merger_spinoff_delisting_ledger_complete": False, "point_in_time_sector_history": False,
            "survivorship_effect_on_returns": "Not identifiable from excluded histories; 33.07% roster attrition is coverage, not a return-bias estimate.",
            "verdict": "EXPLORATORY_REVISED_SURVIVOR_PANEL; no candidate can pass the point-in-time promotion gate"}


def availability_registry(panel, audit):
    definitions = {}
    for name in [*panel["feature_names"], *panel["ta"]]:
        definitions[name] = {"source": "completed adjusted OHLCV through feature session; current revised vendor vintage",
                             "source_timestamp": "XNYS session close", "observed_at": audit["observed_at_max"],
                             "known_at": "historical session close + 1 hour ASSUMED; actual historical observation unverified",
                             "calculated_at": "actual run timestamp in run manifest",
                             "decision_at": "assumed known_at + 1 second for simulation",
                             "earliest_execution_at": "next XNYS session open or later stress fill",
                             "point_in_time_certified": False,
                             "display_offset_used_in_feature": False}
    return {"features": definitions, "strict_use": "require_available rejects actual 2026 observation at an earlier historical decision",
            "universe": "Historical 2012 published rosters, known by March 2013; 85 names selected for retrievable coverage, biased",
            "corporate_actions": "split/dividend-adjusted internally consistent units; revision and distribution-chain risk unresolved",
            "sectors": "not available as historical inputs; optional static attribution only"}
