"""Predeclared descriptive audits; cannot reopen or rescue a failed hypothesis."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantlab.data import ROOT, digest, load_snapshot, save_json
from quantlab.experiment import rank_ic
from .audit import source_hashes
from .execution import simulate
from .governance import append_event, fingerprint, read_ledger
from .metrics import concentration, metrics
from .strategies import make_panel, score_fold, weights_for
from .study import LEDGER, clean, read_config, stitch
from .validation import block_inference


def register_supplement(parent):
    eid = read_config()["study_id"]+"/DESCRIPTIVE_AUDIT_01"
    spec = {"experiment_id": eid, "parent": str(parent.relative_to(ROOT)),
            "hypothesis": "The discovery survivor may depend on sector proxies, a few trades or weak forward rank information. These diagnostics can falsify it; cannot reverse its failed validation.",
            "rationale": "Complete attribution, rank-IC and sector-removal requirements; actual point-in-time sector history unavailable in inspected roster sources.",
            "feature_family": "DESCRIPTIVE_ONLY", "parameters": {"horizon": 10, "tail_fraction": .2, "bootstrap_block": 20},
            "parameter_search_space": "None; one fixed descriptive calculation and every one of eight frozen sector-proxy exclusions",
            "entry_rule": "Existing frozen V2 rules; remove every member of one complete fixed proxy group before ranking",
            "exit_rule": "Existing V2 simulator", "position_sizing": "Existing V2; top10 equal weights, no group renormalization after selection",
            "rebalance_frequency": 5, "benchmark": ["champion", "stock_equal", "refit_ridge", "reversal"],
            "cost_assumptions": read_config()["execution"], "train_period": "Same purged expanding fits as parent",
            "validation_period": "2018-2022; reused and already observed, strictly diagnostic",
            "holdout_status": read_config()["holdout_status"], "maximum_trials_allowed": 1,
            "maximum_simulations": 160, "group_file_hash": digest((ROOT/"config/v2_attribution_groups.json").read_bytes()),
            "source_hash": fingerprint(source_hashes()), "no_promotion_or_selection_use": True}
    append_event(LEDGER, "registered", spec)
    return eid


def load_sim(path):
    result = {}
    for name in ("daily", "contributions", "weights"):
        result[name] = pd.read_csv(path/f"{name}.csv", index_col=0, parse_dates=True)
    for name in ("trades", "roundtrips"):
        try:
            result[name] = pd.read_csv(path/f"{name}.csv")
        except pd.errors.EmptyDataError:
            result[name] = pd.DataFrame()
    return result


def run(parent):
    cfg = read_config()
    if not (parent/"summary.json").exists():
        raise ValueError("Complete parent study first")
    eid = register_supplement(parent)
    out = parent/"supplement"
    out.mkdir(exist_ok=False)
    current, count = None, 0
    try:
        summary = json.loads((parent/"summary.json").read_text())
        names = summary["discovery_pass"]
        group_cfg = json.loads((ROOT/"config/v2_attribution_groups.json").read_text())
        group_map = {s: g for g, syms in group_cfg["groups"].items() for s in syms}
        bars, _, _ = load_snapshot(cfg["snapshot"])
        bars = bars[bars.date <= "2022-12-31"]
        champion = json.loads((ROOT/cfg["universe"]).read_text())
        symbols = champion["universe"]
        if set(symbols) != set(group_map) or sum(map(len, group_cfg["groups"].values())) != len(symbols):
            raise ValueError("Every stock must have exactly one diagnostic group")
        panel = make_panel(bars, symbols, cfg)
        fold_scores, ic_records = {}, []
        all_names = [s["id"] for s in cfg["challengers"]]
        for year in cfg["discovery_years"]+cfg["outer_years"]:
            scores, _ = score_fold(panel, cfg, f"{year}-01-01", champion)
            fold_scores[year] = scores
            phase = "discovery" if year in cfg["discovery_years"] else "validation"
            labels = panel["opens"].shift(-11)/panel["opens"].shift(-1)-1
            exits = pd.Series(labels.index, index=labels.index).shift(-11)
            dates = (labels.index >= f"{year}-01-01") & (exits <= f"{year}-12-31")
            for name in (all_names if phase == "discovery" else names)+["refit_ridge", "momentum", "reversal"]+( ["champion"] if phase == "validation" else []):
                s = scores[name].where(panel["eligible"]).loc[dates]
                y = labels.loc[dates].where(s.notna())
                ic = rank_ic(s, y)
                ic = ic[s.notna().sum(axis=1) >= 5].dropna()
                ranks = s.rank(axis=1, pct=True)
                spread = y.where(ranks > .8).mean(axis=1)-y.where(ranks <= .2).mean(axis=1)
                spread = spread.reindex(ic.index)
                inf = block_inference(ic.to_numpy(), cfg["bootstrap_samples"], cfg["bootstrap_block"], cfg["seed"]) if len(ic) >= 20 else None
                ic_records.append({"phase": phase, "year": year, "strategy": name, "sessions": len(ic),
                                   "rank_ic_mean": float(ic.mean()), "rank_ic_std": float(ic.std()),
                                   "rank_ic_q05": float(ic.quantile(.05)), "rank_ic_median": float(ic.median()), "rank_ic_q95": float(ic.quantile(.95)),
                                   "fraction_ic_positive": float((ic > 0).mean()), "top_minus_bottom_gross_10session": float(spread.mean()),
                                   "ic_bootstrap_lower": float(inf["lower"][0]/252) if inf else None,
                                   "ic_bootstrap_upper": float(inf["upper"][0]/252) if inf else None,
                                   "ic_one_sided_uncorrected_p": float(inf["familywise_p"][0]) if inf else None})
        save_json(out/"rank_ic.json", {"results": clean(ic_records), "limits": "Overlapping 10-session labels; date-block CI, exploratory unadjusted IC p-values. Gross spread excludes trading costs and is not a tradeable portfolio."})
        sector_results = []
        for group, excluded in group_cfg["groups"].items():
            sims = {}
            for n in ["champion", "stock_equal", *names]:
                parts = []
                for y in cfg["outer_years"]:
                    count += 1
                    if count > 160:
                        raise ValueError("Supplement simulation ceiling exceeded")
                    current = {"experiment_id": eid, "group": group, "strategy": n, "year": y, "number": count}
                    append_event(LEDGER, "trial_started", current)
                    targets = weights_for(panel, fold_scores[y], n, cfg, excluded=excluded)
                    parts.append(simulate(panel, targets, f"{y}-01-01", f"{y}-12-31", cfg["execution"]))
                    append_event(LEDGER, "trial_completed", {**current, "net_return": float(parts[-1]["daily"].nav.iloc[-1]-1)})
                    current = None
                sims[n] = stitch(parts)
            for n in names:
                r = sims[n]["daily"].net_return
                sector_results.append({"excluded_group": group, "candidate": n, "names_removed": len(excluded),
                                       "annual_excess_champion": float((r-sims["champion"]["daily"].net_return).mean()*252),
                                       "annual_excess_equal": float((r-sims["stock_equal"]["daily"].net_return).mean()*252),
                                       "metrics": metrics(sims[n], sims["champion"]["daily"].net_return, group_map)})
            print(f"Supplement: excluded {group}", flush=True)
        save_json(out/"sector_exclusions.json", {"classification": group_cfg["classification"], "results": clean(sector_results), "point_in_time_pass": False})
        regime = pd.Series(np.where(panel["market_trend"].shift(1) > 0, "bull", "bear"), index=panel["closes"].index)
        attributed = {}
        for phase in ("discovery", "validation"):
            spy = load_sim(parent/phase/"spy_buy_hold")["daily"].net_return
            for directory in sorted((parent/phase).iterdir()):
                sim = load_sim(directory)
                # SPY attribution is a benchmark exposure, not a stock sector.
                mapping = {"SPY": "Broad market"} if directory.name == "spy_buy_hold" else group_map
                attributed[phase+"/"+directory.name] = {"metrics": metrics(sim, spy, mapping), "concentration": concentration(sim, regime, mapping)}
        save_json(out/"attribution.json", clean(attributed))
        append_event(LEDGER, "supplement_completed", {"experiment_id": eid, "simulations": count, "selection_use": "NONE", "promotion": "NONE", "point_in_time_sector_gate": False})
        print(f"Supplement complete: {count} predeclared sector-proxy simulations", flush=True)
    except Exception as exc:
        append_event(LEDGER, "supplement_failed", {"experiment_id": eid, "trial": current, "simulations_started": count,
                     "error": f"{type(exc).__name__}: {exc}", "partial_results_invalid": True})
        raise


if __name__ == "__main__":
    import sys
    run(ROOT/"runs"/sys.argv[1])
