"""Explicit 2x/3x ALL-IN cost falsification, including participation impact."""
from __future__ import annotations

import json
import sys

from quantlab.data import ROOT, save_json
from .audit import source_hashes
from .execution import simulate
from .governance import append_event, fingerprint
from .strategies import weights_for
from .study import LEDGER, read_config, stitch
from .supplement import load_sim


def run(parent):
    cfg = read_config()
    eid = cfg["study_id"]+"/ALL_IN_COST_FALSIFICATION_01"
    append_event(LEDGER, "registered", {
        "experiment_id": eid, "hypothesis": "The sole discovery survivor may lose its advantage when every cost component, including impact, doubles or triples.",
        "rationale": "Main cost-multiple scenarios scaled spread/commission/slippage and tested impact separately. This closes the combined all-in stress requirement; cannot rescue failed validation.",
        "feature_family": "EXECUTION_FALSIFICATION", "parameters": {"all_in_cost_multiples": [2, 3]},
        "parameter_search_space": "Two fixed pessimistic stresses; no strategy selection",
        "entry_rule": "Original frozen V2 targets and quantities", "exit_rule": "Original V2 rule",
        "position_sizing": "Original top10/equal controls", "rebalance_frequency": 5,
        "benchmark": ["champion", "stock_equal"], "cost_assumptions": {"base": cfg["execution"], "multiply_every_component": True},
        "train_period": "Original purged fits", "validation_period": cfg["outer_years"],
        "holdout_status": cfg["holdout_status"], "maximum_trials_allowed": 1, "maximum_simulations": 30,
        "source_hash": fingerprint(source_hashes()), "post_discovery_falsification": True})
    from quantlab.data import load_snapshot
    from .strategies import make_panel, score_fold
    bars, _, _ = load_snapshot(cfg["snapshot"])
    bars = bars[bars.date <= "2022-12-31"]
    champion = json.loads((ROOT/cfg["universe"]).read_text())
    panel = make_panel(bars, champion["universe"], cfg)
    scores = {y: score_fold(panel, cfg, f"{y}-01-01", champion)[0] for y in cfg["outer_years"]}
    current, count, results = None, 0, []
    try:
        for multiple in (2, 3):
            sims = {}
            for name in ("bb_reversal", "champion", "stock_equal"):
                parts = []
                for year in cfg["outer_years"]:
                    count += 1
                    current = {"experiment_id": eid, "strategy": name, "year": year, "all_in_cost_multiple": multiple, "number": count}
                    append_event(LEDGER, "trial_started", current)
                    targets = weights_for(panel, scores[year], name, cfg)
                    parts.append(simulate(panel, targets, f"{year}-01-01", f"{year}-12-31", cfg["execution"], cost_multiple=multiple, impact_multiple=multiple))
                    append_event(LEDGER, "trial_completed", {**current, "net_return": float(parts[-1]["daily"].nav.iloc[-1]-1)})
                    current = None
                sims[name] = stitch(parts)
            r = sims["bb_reversal"]["daily"].net_return
            results.append({"multiple": multiple, "total_return": float((1+r).prod()-1),
                            "cagr": float((1+r).prod()**(252/len(r))-1),
                            "annual_excess_champion": float((r-sims["champion"]["daily"].net_return).mean()*252),
                            "annual_excess_equal": float((r-sims["stock_equal"]["daily"].net_return).mean()*252)})
        save_json(parent/"all_in_costs.json", {"results": results, "simulations": count, "validation_failure_unchanged": True})
        append_event(LEDGER, "all_in_costs_completed", {"experiment_id": eid, "simulations": count, "results": results})
        print(json.dumps(results, indent=2), flush=True)
    except Exception as exc:
        append_event(LEDGER, "all_in_costs_failed", {"experiment_id": eid, "trial": current, "error": f"{type(exc).__name__}: {exc}"})
        raise


if __name__ == "__main__":
    run(ROOT/"runs"/sys.argv[1])
