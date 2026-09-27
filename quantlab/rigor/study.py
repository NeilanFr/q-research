"""Bounded, resumable only as explicit counted attempts, chronological V2 study."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import traceback

import numpy as np
import pandas as pd

from quantlab.data import ROOT, digest, load_snapshot, now_utc, save_json
from .audit import audit_inputs, availability_registry, source_hashes
from .execution import simulate
from .governance import append_event, fingerprint, read_ledger
from .strategies import RULES, make_panel, score_fold, weights_for
from .metrics import metrics, concentration
from .validation import block_inference, sharpe_evidence

LEDGER = ROOT/"research/v2_ledger.jsonl"
CONFIG = ROOT/"config/rigorous_v2.json"


def read_config():
    cfg = json.loads(CONFIG.read_text())
    if len(cfg["challengers"]) != cfg["maximum_challengers"] or len(set(s["id"] for s in cfg["challengers"])) != len(cfg["challengers"]):
        raise ValueError("Challenger budget/schema mismatch")
    if len(cfg["challengers"])+len(cfg["baselines"])+len(cfg["negative_controls"]) > cfg["maximum_primary_strategies"]:
        raise ValueError("Strategy budget exceeded")
    return cfg


def register():
    cfg = read_config()
    common = {"rationale": cfg["hypothesis"], "parameter_search_space": cfg["parameters"],
              "exit_rule": "Sell at scheduled next-session rebalance when omitted/reweighted; terminal liquidation fixed before evaluation.",
              "position_sizing": "Top 10 equal weight, gross 99.5%, 40% cap when fewer names; no shorts; quantities fixed at previous close.",
              "rebalance_frequency": "Every five XNYS sessions, anchored at each independent annual fold start; costs on initial/final fills. Fold notional resets to USD 1m; stitched returns are a normalized index, not a continuous funded account.",
              "benchmark": cfg["baselines"], "cost_assumptions": cfg["execution"],
              "train_period": {"start": cfg["train_start"], "end": "purged/embargoed before each inner/outer year", "horizon": cfg["horizon"], "embargo": cfg["embargo_sessions"]},
              "validation_period": {"discovery": cfg["discovery_years"], "outer": cfg["outer_years"], "inner": cfg["inner_selection"]},
              "holdout_status": cfg["holdout_status"], "maximum_trials_allowed": 1,
              "config_sha256": digest(CONFIG.read_bytes()), "maximum_simulations_study": cfg["maximum_simulations"],
              "robustness": cfg["stress_scenarios"], "gates": {k: cfg[k] for k in ("discovery_gate", "validation_gate", "robustness_gate", "prospective")}}
    existing = {r["payload"].get("experiment_id") for r in read_ledger(LEDGER) if r["kind"] == "registered"}
    specs = [*cfg["challengers"], *({"id": n, "family": "BASELINE_OR_NEGATIVE_CONTROL", "rule": n} for n in cfg["baselines"]+cfg["negative_controls"])]
    for spec in specs:
        eid = cfg["study_id"]+"/"+spec["id"]
        if eid in existing:
            continue
        append_event(LEDGER, "registered", {**common, "experiment_id": eid,
                     "hypothesis": RULES.get(spec["rule"], "Matched-framework predeclared reference/control: "+spec["rule"]),
                     "feature_family": spec["family"], "parameters": spec,
                     "entry_rule": RULES.get(spec["rule"], spec["rule"])+"; execute only after completed feature bar."})
    print(f"Registered {len(specs)} strategies, {len(cfg['challengers'])} challengers. No outcomes computed.", flush=True)


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def stitch(sims):
    """Independent annual folds, chained normalized index and dollar attribution."""
    parts, cons, trades, lots, weights = [], [], [], [], []
    capital = 1.
    for sim in sims:
        daily = sim["daily"].copy()
        daily[["nav_before", "nav"]] *= capital
        parts.append(daily)
        cons.append(sim["contributions"]*capital)
        weights.append(sim["weights"])
        tr, rt = sim["trades"].copy(), sim["roundtrips"].copy()
        if len(tr):
            tr[["dollars_per_initial_dollar", "fee"]] *= capital
        if len(rt):
            rt["pnl"] *= capital
        trades.append(tr)
        lots.append(rt)
        capital = daily.nav.iloc[-1]
    return {"daily": pd.concat(parts), "contributions": pd.concat(cons), "weights": pd.concat(weights),
            "trades": pd.concat(trades, ignore_index=True), "roundtrips": pd.concat(lots, ignore_index=True)}


def run():
    cfg = read_config()
    ledger = read_ledger(LEDGER)
    registrations = [r for r in ledger if r["kind"] == "registered" and r["payload"]["experiment_id"].startswith(cfg["study_id"]+"/")]
    if len(registrations) != cfg["maximum_primary_strategies"] or any(r["payload"]["config_sha256"] != digest(CONFIG.read_bytes()) for r in registrations):
        raise ValueError("Register the exact configuration BEFORE running; modifications need a new study ID")
    attempts = [r for r in ledger if r["kind"] == "attempt_started" and r["payload"]["study_id"] == cfg["study_id"]]
    if len(attempts) >= cfg["maximum_operational_attempts"]:
        raise ValueError("Operational attempt budget exhausted")
    if any(r["kind"] == "study_completed" and r["payload"]["study_id"] == cfg["study_id"] for r in ledger):
        raise ValueError("Study already complete; use archived artifacts, never rerun to improve a result")
    run_id = f"{cfg['study_id']}_attempt{len(attempts)+1}"
    out = ROOT/"runs"/run_id
    out.mkdir(parents=True, exist_ok=False)
    hashes = source_hashes()
    append_event(LEDGER, "attempt_started", {"study_id": cfg["study_id"], "run_id": run_id,
                 "source_hash": fingerprint(hashes), "config_hash": digest(CONFIG.read_bytes())})
    save_json(out/"source_hashes.json", hashes)
    save_json(out/"config.json", cfg)
    # Preserve source for exact reruns in isolated checkouts without altering this ledger.
    for rel in hashes:
        destination = out/"source"/rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT/rel).read_bytes())
    count = 0
    current = None
    def evaluate(panel, scores, name, year, **stress):
        nonlocal count, current
        count += 1
        if count > cfg["maximum_simulations"]:
            raise ValueError("Hard simulation budget exceeded")
        current = {"name": name, "year": year, "stress": stress, "simulation_number": count}
        append_event(LEDGER, "trial_started", {"run_id": run_id, **current})
        k = stress.pop("top_k", None)
        start = max(f"{year}-01-01", stress.pop("start", f"{year}-01-01"))
        excluded = stress.pop("excluded", ())
        targets = weights_for(panel, scores, name, cfg, k, excluded)
        p = panel["spy"] if name == "spy_buy_hold" else panel
        sim = simulate(p, targets, start, f"{year}-12-31", cfg["execution"], cadence=stress.pop("cadence", cfg["cadence"]), buy_hold=name == "spy_buy_hold", **stress)
        append_event(LEDGER, "trial_completed", {"run_id": run_id, **current, "net_return": float(sim["daily"].nav.iloc[-1]-1)})
        current = None
        return sim
    try:
        bars, manifest, _ = load_snapshot(cfg["snapshot"])
        champion = json.loads((ROOT/cfg["universe"]).read_text())
        audit = audit_inputs(bars, manifest, champion)
        save_json(out/"input_audit.json", audit)
        bars = bars[bars.date <= f"{max(cfg['outer_years'])}-12-31"].copy()
        symbols = champion["universe"]["symbols"] if isinstance(champion["universe"], dict) else champion["universe"]
        panel = make_panel(bars, symbols, cfg)
        save_json(out/"feature_availability.json", availability_registry(panel, audit))
        names = [s["id"] for s in cfg["challengers"]]
        refs = cfg["baselines"]+cfg["negative_controls"]
        folds, discovery, fold_scores, model_records = {}, {}, {}, {}
        for year in cfg["discovery_years"]:
            scores, models = score_fold(panel, cfg, f"{year}-01-01", champion)
            fold_scores[year] = scores
            model_records[str(year)] = clean(models)
            # Frozen 2017-fit champion is INVALID for earlier years; do not score it.
            folds[year] = {n: evaluate(panel, scores, n, year) for n in names+refs if n != "champion"}
            print(f"Discovery {year}: {len(folds[year])} strategies; {count} simulations", flush=True)
        discovery = {n: stitch([folds[y][n] for y in cfg["discovery_years"]]) for n in names+refs if n != "champion"}
        dmetrics = {n: metrics(sim, discovery["spy_buy_hold"]["daily"].net_return) for n, sim in discovery.items()}
        survivors, dgates = [], {}
        for name in names:
            r = discovery[name]["daily"].net_return
            gate = cfg["discovery_gate"]
            comparisons = {ref: float((r-discovery[ref]["daily"].net_return).mean()*252) for ref in ("stock_equal", "momentum", "random")}
            years = (r-discovery["stock_equal"]["daily"].net_return).groupby(r.index.year).mean()
            checks = {"economic_increment": min(comparisons.values()) > gate["annual_mean_excess_over_equal_momentum_random"],
                      "positive_years": int((years > 0).sum()) >= gate["positive_years_vs_equal"],
                      "positive_net": dmetrics[name]["total_return"] > 0,
                      "trade_count": dmetrics[name]["trade_count"] >= gate["min_closed_lots"],
                      "drawdown": dmetrics[name]["max_drawdown"] > gate["drawdown_floor"]}
            passed = all(checks.values())
            dgates[name] = {"passed": passed, "checks": checks, "comparisons": comparisons}
            if passed:
                survivors.append(name)
            append_event(LEDGER, "discovery_result", {"experiment_id": cfg["study_id"]+"/"+name, **clean(dgates[name]), "metrics": dmetrics[name]})
        print(f"Discovery gate: {len(survivors)}/{len(names)} challengers pass. Failed hypotheses stay failed.", flush=True)
        save_json(out/"discovery_metrics.json", clean(dmetrics))
        save_json(out/"discovery_gates.json", clean(dgates))
        # Always finish baseline/control chronological audit; failed challengers do not enter validation.
        selections = []
        outer_folds = {}
        for year in cfg["outer_years"]:
            inner_year = year-1
            if inner_year not in folds:
                s, m = score_fold(panel, cfg, f"{inner_year}-01-01", champion)
                fold_scores[inner_year] = s
                model_records[str(inner_year)] = clean(m)
                folds[inner_year] = {n: evaluate(panel, s, n, inner_year) for n in survivors+["refit_ridge"]}
            for family in sorted(set(s["family"] for s in cfg["challengers"])):
                eligible = [s["id"] for s in cfg["challengers"] if s["family"] == family and s["id"] in survivors]
                choice = sorted(eligible, key=lambda n: (-float((folds[inner_year][n]["daily"].net_return-folds[inner_year]["refit_ridge"]["daily"].net_return).mean()), n))[0] if eligible else None
                selections.append({"outer_year": year, "inner_year": inner_year, "family": family, "selected": choice})
            scores, models = score_fold(panel, cfg, f"{year}-01-01", champion)
            fold_scores[year] = scores
            model_records[str(year)] = clean(models)
            outer_folds[year] = {n: evaluate(panel, scores, n, year) for n in survivors+refs}
            folds[year] = outer_folds[year]
            print(f"Outer {year}: {len(outer_folds[year])} strategies; {count} simulations", flush=True)
        validation = {n: stitch([outer_folds[y][n] for y in cfg["outer_years"]]) for n in survivors+refs}
        vmetrics = {n: metrics(sim, validation["spy_buy_hold"]["daily"].net_return) for n, sim in validation.items()}
        save_json(out/"fold_models.json", model_records)
        save_json(out/"inner_selections.json", selections)
        save_json(out/"validation_metrics.json", clean(vmetrics))
        # Full discovery family enters multiple-testing audit, including losing variants.
        dx = pd.DataFrame({n: discovery[n]["daily"].net_return-discovery["stock_equal"]["daily"].net_return for n in names})
        di = block_inference(dx, cfg["bootstrap_samples"], cfg["bootstrap_block"], cfg["seed"])
        vi = None
        if survivors:
            vx = pd.DataFrame({n: validation[n]["daily"].net_return-validation["champion"]["daily"].net_return for n in survivors})
            vi = block_inference(vx, cfg["bootstrap_samples"], cfg["bootstrap_block"], cfg["seed"])
        evidence = {"discovery": {"candidates": names, **clean(di)}, "validation": {"candidates": survivors, **clean(vi)} if vi else None,
                    "prior_search_count": audit["prior_trial_rows"], "limits": "Exploratory reused survivor history; no p-value repairs data vintage or prior adaptive search."}
        trial_sharpes = [discovery[n]["daily"].net_return.mean()/discovery[n]["daily"].net_return.std() for n in names]
        evidence["iid_sharpe_diagnostics"] = {n: sharpe_evidence(discovery[n]["daily"].net_return, trial_sharpes, len(names)+sum(audit["prior_trial_rows"].values())) for n in names}
        save_json(out/"multiple_testing.json", clean(evidence))
        vgates = {}
        for j, name in enumerate(survivors):
            r = validation[name]["daily"].net_return
            gate = cfg["validation_gate"]
            comparison = {ref: float((r-validation[ref]["daily"].net_return).mean()*252) for ref in ("champion", "stock_equal", "momentum", "random")}
            yearly = (r-validation["champion"]["daily"].net_return).groupby(r.index.year).mean()
            checks = {"economic_increment": min(comparison.values()) > gate["annual_mean_excess_over_champion_equal_momentum_random"],
                      "chronological_stability": int((yearly > 0).sum()) >= gate["positive_years_vs_champion"],
                      "positive_net": vmetrics[name]["total_return"] > 0,
                      "drawdown": vmetrics[name]["max_drawdown"] > gate["drawdown_floor"],
                      "trade_count": vmetrics[name]["trade_count"] >= gate["min_closed_lots"],
                      "bootstrap": vi["lower"][j] > gate["bootstrap_lower_excess"],
                      "multiplicity": vi["familywise_p"][j] <= gate["familywise_p_max"]}
            vgates[name] = {"passed": all(checks.values()), "checks": checks, "comparisons": comparison}
            append_event(LEDGER, "validation_result", {"experiment_id": cfg["study_id"]+"/"+name, **clean(vgates[name]), "metrics": vmetrics[name]})
        save_json(out/"validation_gates.json", clean(vgates))
        # Discovery survivors are 'promising' even when validation later rejects them.
        robustness = robustness_audit(panel, bars, champion, cfg, survivors, fold_scores, validation, evaluate, out)
        regimes = pd.Series(np.where(panel["market_trend"].shift(1) > 0, "bull", "bear"), index=panel["closes"].index)
        for phase, sims in (("discovery", discovery), ("validation", validation)):
            for n, sim in sims.items():
                p = out/phase/n
                p.mkdir(parents=True)
                for key in ("daily", "weights", "contributions", "trades", "roundtrips"):
                    sim[key].to_csv(p/f"{key}.csv", index=key not in {"trades", "roundtrips"})
                save_json(p/"concentration.json", clean(concentration(sim, regimes)))
        # Selected outer procedures are reported separately, not chosen after outer performance.
        procedures = {}
        for family in sorted(set(s["family"] for s in cfg["challengers"])):
            family_selections = [s for s in selections if s["family"] == family]
            if all(s["selected"] for s in family_selections):
                sim = stitch([outer_folds[s["outer_year"]][s["selected"]] for s in family_selections])
                procedures[family] = metrics(sim, validation["spy_buy_hold"]["daily"].net_return)
        save_json(out/"nested_procedure_metrics.json", clean(procedures))
        summary = {"study_id": cfg["study_id"], "run_id": run_id, "completed_at": now_utc(),
                   "strategies_registered": cfg["maximum_primary_strategies"], "challengers": len(names),
                   "simulations": count, "discovery_pass": survivors, "validation_pass": [n for n, g in vgates.items() if g["passed"]],
                   "robustness": robustness, "point_in_time_data_pass": False, "frozen_challengers": [],
                   "prospective": "No challenger eligible for freeze; champion remains unchanged. No broker calls.",
                   "promotion": "NONE", "champion": "COMPETITION_V1 / ridge10", "source_hash": fingerprint(hashes)}
        save_json(out/"summary.json", clean(summary))
        append_event(LEDGER, "study_completed", clean(summary))
        print(json.dumps(clean(summary), indent=2), flush=True)
        return out
    except Exception as exc:
        (out/"error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        append_event(LEDGER, "attempt_failed", {"study_id": cfg["study_id"], "run_id": run_id, "trial": current,
                     "simulations_started": count, "error": f"{type(exc).__name__}: {exc}", "all_partial_results_invalid_for_selection": True})
        raise


def robustness_audit(panel, bars, champion, cfg, names, fold_scores, validation, evaluate, out):
    records = []
    if not names:
        save_json(out/"robustness.json", {"status": "NOT_TRIGGERED_NO_DISCOVERY_SURVIVORS", "results": []})
        return {"tested": 0, "passed": []}
    perturb_panel = make_panel(bars, panel["closes"].columns.tolist(), cfg, perturb=True)
    perturb_scores = {y: score_fold(perturb_panel, cfg, f"{y}-01-01", champion)[0] for y in cfg["outer_years"]}
    for scenario in cfg["stress_scenarios"]:
        options = {k: v for k, v in scenario.items() if k != "id"}
        perturbed = options.pop("perturb", False)
        p = perturb_panel if perturbed else panel
        scores = perturb_scores if perturbed else fold_scores
        years = [y for y in cfg["outer_years"] if y >= int(options.get("start", "0000")[:4])]
        sims = {n: stitch([evaluate(p, scores[y], n, y, **options) for y in years]) for n in ["champion", "stock_equal", *names]}
        ref = sims["champion"]["daily"].net_return
        for n in names:
            r = sims[n]["daily"].net_return
            records.append({"scenario": scenario["id"], "candidate": n, "annual_excess_champion": float((r-ref).mean()*252),
                            "annual_excess_equal": float((r-sims["stock_equal"]["daily"].net_return).mean()*252),
                            "total_return": float((1+r).prod()-1)})
        print(f"Robustness {scenario['id']}: completed", flush=True)
    # Attribution deletions are not executable reruns; explicitly labelled sensitivity.
    for n in names:
        sim, reference = validation[n], validation["champion"]
        r, ref = sim["daily"].net_return, reference["daily"].net_return
        yearly = r.groupby(r.index.year).apply(lambda x: (1+x).prod()-1)
        for label, year in (("remove_best_year", yearly.idxmax()), ("remove_worst_year", yearly.idxmin())):
            mask = r.index.year != year
            records.append({"scenario": label, "candidate": n, "annual_excess_champion": float((r-ref)[mask].mean()*252), "removed_year": int(year)})
        lots = sim["roundtrips"]
        winner_pnl = lots.nlargest(max(1, int(np.ceil(len(lots)*.01))), "pnl").pnl.sum() if len(lots) else 0
        records.append({"scenario": "remove_top_1pct_lot_pnl_attribution_only", "candidate": n,
                        "remaining_net_pnl": float(sim["daily"].nav.iloc[-1]-1-winner_pnl)})
        vol = panel["market_vol"].shift(1)
        # Trailing median, never a full-sample regime threshold.
        high = vol > vol.rolling(252).median()
        bull = panel["market_trend"].shift(1) > 0
        for label, mask in (("high_vol", high), ("low_vol", ~high), ("bull", bull), ("bear", ~bull)):
            rr = (r-ref)[mask.reindex(r.index).fillna(False)]
            records.append({"scenario": label, "candidate": n, "annual_excess_champion": float(rr.mean()*252), "sessions": len(rr)})
    save_json(out/"robustness.json", {"results": clean(records), "sector_removal": "BLOCKED: no point-in-time sector history; no robustness pass can be asserted", "selection_use": "falsification only"})
    return {"tested": len(names), "passed": [], "sector_gate": "unverified"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("register", "run"))
    args = parser.parse_args()
    register() if args.command == "register" else run()


if __name__ == "__main__":
    main()
