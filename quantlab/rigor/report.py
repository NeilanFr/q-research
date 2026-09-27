"""Publish compact, reproducible research evidence without raw prices or state."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from quantlab.data import ROOT, digest, now_utc, save_json
from .governance import append_event, fingerprint, read_ledger
from .study import LEDGER, clean, read_config


def percent(v):
    return "n/a" if v is None else f"{v*100:.2f}%"


def number(v):
    return "n/a" if v is None else f"{v:.3f}"


def publish(parent):
    cfg = read_config()
    def read(name):
        return json.loads((parent/name).read_text())
    summary, audit = read("summary.json"), read("input_audit.json")
    discovery, validation = read("discovery_metrics.json"), read("validation_metrics.json")
    dgates, vgates = read("discovery_gates.json"), read("validation_gates.json")
    stats, robustness = read("multiple_testing.json"), read("robustness.json")
    attribution, sectors = read("supplement/attribution.json"), read("supplement/sector_exclusions.json")
    ic, reproduction = read("supplement/rank_ic.json"), read("reproducibility.json")
    all_in_costs = read("all_in_costs.json")
    evidence = {"published_at": now_utc(), "summary": summary, "data_audit": audit,
                "counts": {"primary_strategies": 32, "challenger_strategies": 20, "feature_combinations": 9,
                           "primary_indicator_parameter_sets": {"RSI": 3, "BOLLINGER": 1, "ICHIMOKU": 1},
                           "main_simulations": 362, "sector_proxy_simulations": 120, "reproduction_simulations": 2,
                           "all_in_cost_simulations": 30, "total_simulations": 514, "failed_discovery_hypotheses": 19, "failed_validation_hypotheses": 1,
                           "discovery_passes": 1, "validation_passes": 0, "promotions": 0},
                "discovery_metrics": discovery, "discovery_gates": dgates, "validation_metrics": validation,
                "validation_gates": vgates, "multiple_testing": stats, "robustness": robustness,
                "sector_exclusions": sectors, "rank_ic": ic, "attribution": attribution, "all_in_costs": all_in_costs,
                "nested_selection": read("inner_selections.json"), "nested_procedures": read("nested_procedure_metrics.json"),
                "reproduction": reproduction, "source_hashes": read("source_hashes.json"),
                "config_hash": digest((parent/"config.json").read_bytes()),
                "results_invalidated": ["Unbiased point-in-time causal-alpha interpretation of the revised survivor panel",
                                        "Exact executable-opening-share interpretation of legacy opening-NAV target sizing"],
                "scope": "Negative exploratory conclusion; no frozen champion record overwritten; no live or PAPER orders"}
    output = ROOT/"research/v2_results.json"
    save_json(output, clean(evidence))
    rows = []
    for spec in cfg["challengers"]:
        n = spec["id"]
        m = discovery[n]
        verdict = "Discovery PASS; validation FAIL" if n in vgates else "Discovery FAIL; validation not run"
        rows.append(f"| {n} | {spec['family']} | {percent(m['cagr'])} | {number(m['sharpe'])} | {percent(m['max_drawdown'])} | {verdict} |")
    baselines = []
    for n in ["bb_reversal", "champion", "refit_ridge", "stock_equal", "spy_buy_hold", "momentum", "reversal", "random", "permuted_features", "randomized_timestamps", "ridge_delay1", "ridge_delay5", "no_trade"]:
        m = validation[n]
        baselines.append(f"| {n} | {percent(m['cagr'])} | {number(m['sharpe'])} | {percent(m['max_drawdown'])} | {m['annual_turnover']:.2f} |")
    stress = []
    for r in robustness["results"]:
        if "annual_excess_champion" in r:
            stress.append(f"| {r['scenario']} | {percent(r['annual_excess_champion'])} |")
    concentration = attribution["validation/bb_reversal"]["concentration"]["top_trades"]
    concentration_rows = [f"| {k} | {v['count']} | {percent(v['fraction_net'])} | {percent(v['fraction_positive'])} |" for k, v in concentration.items()]
    sector_rows = [f"| {r['excluded_group']} | {r['names_removed']} | {percent(r['annual_excess_champion'])} | {percent(r['annual_excess_equal'])} |" for r in sectors["results"]]
    full_metrics = attribution["validation/bb_reversal"]["metrics"]
    metric_rows = [f"| {k} | {v if isinstance(v,str) else number(v) if v is not None else 'n/a'} |" for k, v in full_metrics.items()]
    report = f"""# Technical indicator research — V2

**No challenger qualifies for promotion. COMPETITION_V1 / ridge10 is unchanged.**
Twenty predeclared challengers were screened. Nineteen failed discovery; the
Bollinger mean-reversion survivor failed the validation uncertainty and
multiple-testing gates. No candidate was frozen. No broker orders were sent.

This is exploratory falsification on a revised survivor panel, not evidence of
an unbiased causal trading edge. It cannot bypass the research constitution's
point-in-time-data gate. The [machine-readable results](../research/v2_results.json)
contain every metric, gate, fold selection, rank-IC diagnostic and attribution.
The [append-only ledger](../research/v2_ledger.jsonl) preserves registration before
outcomes and every started/completed trial. Large daily artifacts remain local at
`runs/{parent.name}`.

## Design and search budget

The registered study is `{cfg['study_id']}`. Discovery consists of independently
funded 2015, 2016 and 2017 folds, with expanding purged past-only ridge fits.
Each 2018-2022 outer year has a previous-year inner selection period; model and
preprocessing parameters are fitted anew using only earlier data. Family
selection maximizes inner excess against refit ridge, with deterministic ties.
Only discovery survivors enter outer evaluation. The Bollinger family had one
survivor, so its inner selection was degenerate; the engine did not invent other
contenders. Other families have no selected outer procedure.

Every fold resets reference notional to USD 1m, liquidates at its predetermined
last close and pays both entry and exit costs. Stitched results are a normalized
index of independent annual experiments, not one continuous funded account.
Rebalance anchors reset by fold. Consequently the champion's V2 comparator
differs from its original continuous backtest; the original 16.84% validation
CAGR remains archived and is not replaced by the V2 diagnostic.

The data begins in 2010; training starts April 2013. Ten-session labels enter
next open and exit open t+11; purge removes boundary overlaps and adds ten
sessions of embargo. RSI periods are 7/14/21; Bollinger 20/2; Ichimoku 9/26/52.
The 20 challengers contain nine combinations/ablations. There are eight references
and four negative controls. Main study: 362 simulations; preregistered descriptive
sector-proxy supplement: 120; all-in cost audit: 30; exact archived-source
replications: two. Total 514,
including references and stress cases, not 484 independent strategies. There was
one successful operational attempt and no failed operational run. Prior studies
retain their 378 complete, three failed and six unrun trial rows.

2018-2022 was already reused. 2023 through September 18, 2026 was opened on
September 21 and is permanently opened; V2 did not rescore it. Outer periods are
chronologically withheld inside this execution, not historically untouched by
the research project.

## All challenger outcomes

| Challenger | Family | Discovery CAGR | Sharpe | Max drawdown | Gate result |
|---|---|---:|---:|---:|---|
{chr(10).join(rows)}

RSI14 and RSI21 reversal had positive screening results but fell short of the
predeclared 1 percentage-point annual mean improvement over momentum; their
increments were about 0.66 and 0.86 points. Their thresholds were not relaxed.
Ichimoku, its market filter and both technical combinations failed discovery.
The best model-block ablation by screening CAGR was base + all TA at 14.26%,
versus refit ridge at 11.11%; it still failed the registered baseline comparisons.
That descriptive increment is not reliable incremental predictive value.

## Matched outer framework

| Strategy/reference | CAGR | Sharpe | Max drawdown | Annual two-way turnover |
|---|---:|---:|---:|---:|
{chr(10).join(baselines)}

Bollinger reversal's annual **arithmetic mean excess** over the champion was
10.00 percentage points, with a 95% paired 20-session block-bootstrap interval
of **−4.12 to +24.09 points**. Its survivor-only max-mean bootstrap p-value was
0.0689; the full 20-candidate discovery Reality Check-style global p-value was
0.7542. Neither establishes significance. The outer p-value does not correct
all prior historical search or discovery filtering. IID PSR/DSR are retained
only as assumption-dependent diagnostics in the machine-readable results.

Simple reversal was close to Bollinger reversal (18.35% versus 19.92% CAGR),
and permuted-feature selections earned 13.09%. Performance must be assessed
against these controls, market beta, and sample uncertainty, not only against
the weakest comparator. No out-of-sample combined model passed discovery, so no
later feature-ablation result is presented as confirmed incremental TA value.

## Falsification and robustness

All declared cost, delay, missed-fill, impact, cadence, holding-count and joint
parameter-perturbation checks were run on the discovery survivor, despite its
later validation failure. Entries below are annual arithmetic mean excess over
the matching stressed champion diagnostic, not CAGR differences. Top-5/top-20
diagnostics change both comparison portfolios' holding counts. The additional
start-date run begins in 2020. Removing years and trades is attribution
sensitivity, not a new executable policy or selectable variant.

| Scenario | Annual mean excess over champion |
|---|---:|
{chr(10).join(stress)}

At 3x non-impact costs the survivor still earned positive total return, but its
mean excess over equal weight narrowed to 1.57 percentage points. Shifting the
rebalance anchor reduced excess over the champion from 10.00 to 3.94 points.
High-volatility excess was 3.34 points versus 19.11 in low volatility. These are
meaningful dependencies, even without a sign reversal. No formal robustness
pass is asserted: statistical validation and point-in-time data/sector evidence
are missing.

A separately preregistered combined all-in cost audit then scaled **every**
component, including impact. At 2x all-in costs CAGR was 14.91%, with annual mean
excess over equal weight of 4.37 points. At 3x all-in costs CAGR was 10.13%, and
that excess narrowed to **0.33 points**. This materially thin margin strengthens
the rejection; it does not alter any original trial or selection rule. Those
30 additional simulations are included in the total above.

The eight groups below were declared **after the main screen** and before their
120 exclusion simulations. They are static analyst-defined sector proxies,
not historical GICS classifications. They can falsify concentration dependence;
they cannot repair the historical sector-data gap or rescue the failed candidate.

| Excluded proxy group | Stocks removed | Mean excess vs champion | Mean excess vs equal weight |
|---|---:|---:|---:|
{chr(10).join(sector_rows)}

## Winner concentration and complete survivor metrics

Trades here are closed FIFO lot fragments, including partial rebalances. There
are 4,353 nonzero fill records and 2,550 closed fragments in outer evaluation;
these are not 2,550 independent bets. Average holding time is adjusted-unit
weighted across those fragments. Metrics of winners/losers use net lot returns.

| Largest winning fragments | Count | Fraction of cumulative net P&L | Fraction of positive P&L |
|---|---:|---:|---:|
{chr(10).join(concentration_rows)}

Net-P&L shares can exceed 100% because losses offset winners. The top three
fragments account for 11.11% of net P&L, while the top 1% account for 60.52%.
Removing that top 1% leaves +0.5834 per initial dollar before any reallocation;
that arithmetic deletion is not an executable backtest. Static Technology
attribution contributes about 49.0% of total net P&L; maximum single-proxy-sector
portfolio exposure reached 99.5%. This is substantial sector concentration.
Ticker, year, month and causal market-regime dollar attribution are preserved in
the compact result file. RSI/Bollinger/Ichimoku rank-IC distributions, date-block
intervals and gross top-minus-bottom ten-session spreads are included too.
IC significance is exploratory and unadjusted; overlapping labels are disclosed.

Raw metric fractions below use decimal units (for example, 0.1992 CAGR = 19.92%).

| Metric | Value |
|---|---:|
{chr(10).join(metric_rows)}

## Reproduction and prospective decision

The archived source, config and raw snapshot hashes reproduce all five 2018
model fits and 251 daily observations for both champion and Bollinger reversal.
Maximum absolute daily-return discrepancy is below 1e−16 (CSV roundoff).
Tests detect deliberate future-return, same-bar, backward Senkou/Chikou,
global-normalization and future-membership leaks. All original PAPER tests pass.

No challenger passed all historical/data gates, so none entered a frozen
prospective comparison. The existing champion has one immutable forecast and
zero evaluated model outcomes as of this audit; its next scheduled rebalance
requires September 28 completed data. No future observations or simulated fills
were written as actual PAPER fills. Champion retention is continuity of the
existing policy, not a new claim that its causal edge has been proven.
"""
    (ROOT/"docs/TECHNICAL_INDICATOR_RESEARCH.md").write_text(report, encoding="utf-8")
    finding = {"kind": "V2_AUDIT_INVALID_INTERPRETATIONS", "champion_record_preserved": True,
               "invalid": evidence["results_invalidated"], "new_numerical_results": "Exploratory only; no promotion",
               "holdout": "permanently opened", "publication_hash": digest(output.read_bytes())}
    previous = read_ledger(LEDGER)
    if not any(r["kind"] == "audit_finding" and r["payload"].get("kind") == finding["kind"] for r in previous):
        append_event(LEDGER, "audit_finding", finding)
    append_event(LEDGER, "report_published", {"study_id": cfg["study_id"], "path": "research/v2_results.json",
                 "sha256": digest(output.read_bytes()), "total_simulations": evidence["counts"]["total_simulations"]})
    print(f"Published {output.relative_to(ROOT)} and technical research report", flush=True)


if __name__ == "__main__":
    import sys
    publish(ROOT/"runs"/sys.argv[1])
