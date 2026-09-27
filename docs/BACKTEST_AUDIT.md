# Backtest audit — V2

Audit date: September 27, 2026. **No promotable challenger or proven causal edge.**
This audit preserves the champion and every old numerical result. It distinguishes
temporal code correctness from data provenance, statistical evidence and actual
executability. Passing one category cannot substitute for the others.

## Findings and invalid interpretations

| Finding | Evidence and disposition |
|---|---|
| Holdout status was inconsistent | README still described 2023+ as sealed after `working_model_holdout_opened` at 2026-09-21T22:05:31.372469+00:00. Current documentation and `config/research_history.json` permanently mark it opened. Archived reports/logs remain unchanged. Legacy frozen guard messages still say “sealed”; those guards prohibit rescoring and do not reseal the data. |
| Future availability affected the roster | 127 identities in published 2012 QQQ/DIA rosters became 85 research stocks through retrievable-history and lineage screening. 42 identities (33.07%) are excluded. This is quantified coverage loss, not an estimated return bias. **Unbiased survivor-free historical-alpha interpretation: INVALID.** |
| Historical observations are not point-in-time | Vendor fetches occurred in 2026. The old bar date cannot establish when an adjusted value was known. The new registry preserves actual observed-at times and flags historical knowledge times as assumed. Strict availability rejects their use as certified historical observations. **Point-in-time causal-edge/promotion interpretation: INVALID.** |
| Corporate-action coverage is incomplete | The frozen snapshot contains 4,930 dividend and 64 split events. Adjusted-open identity error is at most about 1.41e-8 from serialization. High/low/open/close use a common factor in V2. Merger, spinoff, symbol-lineage and delisting outcome chains remain incomplete; no dividends are double-added to adjusted-unit returns. |
| Exact opening target sizing was optimistic | `working_model.simulate` and `aggressive.simulate_close` use actual opening NAV to achieve target weights. Signals are previous-close causal, but final opening quantities are not known before a pre-open order cutoff. **Exact executable opening-share interpretation: INVALID.** V2 freezes quantities using prior-close information, then allows only affordability/participation reductions. Old numbers are preserved. |
| Champion cannot be historical OOS before 2018 | Its frozen coefficients were fitted through 2017. V2 excludes frozen-champion discovery results in 2015-2017; refit ridge is a separate past-only reference. No early-period champion OOS result was generated. |
| Sector timing is unavailable | Inspected historical roster schedules establish identities, not a complete dated sector history. A separately preregistered eight-group static proxy audit is descriptive only. Historical sector causality and a formal sector robustness pass remain unverified. |
| Old and new simulation claims differ | Core ETF rules fill next open; overnight rules use an earlier completed close for subsequent close entry; the existing open/close guards were retained. No confirmed same-close signal/fill bug was found in those paths. New timing tests reject deliberate same-bar injection. |
| Selection history is material | Seven prior runs contain 387 trial rows, including failures. V2 tests 20 challengers and retains all failures. Reused validation and previously opened history cannot become fresh confirmation. |
| V2 rejection is statistical as well as data-related | Only Bollinger reversal passed the economic discovery screen. Outer CI crosses zero and p=0.0689 exceeds 0.05. Discovery familywise global p=0.7542. No gate was relaxed after seeing results. |

Historical roster sources are the [QQQ 2012 annual schedule](https://www.sec.gov/Archives/edgar/data/1067839/000110465913005075/a12-24340_1n30b2.htm)
and [DIA 2013 prospectus containing the 2012 schedule](https://www.sec.gov/Archives/edgar/data/1041130/000119312513076798/d477369d497.htm).
The existing manifest retains filing availability dates and unresolved issuers,
including bankruptcy and reused-symbol cases. No missing terminal return was
replaced by zero; no acquirer history was substituted to improve results. IPO/
history warmup requires 200 prior sessions. Later IPOs are outside the fixed
2012-roster experiment, not evidence about a complete investable US universe.

## Requirement evidence

| Requirement | Authoritative evidence / status |
|---|---|
| Preserve repository and champion | Frozen source and artifact hashes verified before study, in `input_audit.json`, and after experiments. No frozen source/config/broker files changed. |
| Preregister hypotheses and count failures | `research/v2_ledger.jsonl`: 32 primary registrations before any outcomes; 20 challenger discovery results; 19 discovery failures and one validation failure. Descriptive supplements and replications have separate prior registrations. |
| Small hard search budget | `config/rigorous_v2.json`: 20 challengers, 32 primary strategies, conventional parameters, fixed perturbation, 2,500 main-simulation ceiling; no threshold relaxation. |
| Feature availability | `runs/V2_TA_20260927_01_attempt1/feature_availability.json`; strict timestamps and effective/known metadata logic in `governance.py`. Data-quality gate is false. |
| Future-data invariance | `tests/test_rigor.py`: mutate/delete future prices and volume, compare technical/base features, training transforms, predictions, eligibility, targets and intended orders. Metadata tests cover future membership, sectors and five corporate-action types. |
| Deliberate leakage injections | The same invariant assertions detect future returns, backward Senkou and Chikou, global normalization and future membership; execution timing rejects same-bar injection. |
| Preprocessing and model refits | `validation.py`, archived `fold_models.json`, and five fit reproductions from archived source. Training-only quantiles/means/scales; original champion coefficients remain separate. |
| Purging and embargo | Ten-session open-to-open labels; t+11 label exit; ten-session additional embargo. Boundary tests inspect actual exit positions. |
| Nested chronological selection | Archived `inner_selections.json`, `nested_procedure_metrics.json`, explicit prior-year inner selection and untouched-within-run outer year. Project-level historical reuse is prominently disclosed. |
| Baselines and negative controls | SPY, liquid equal weight, frozen champion, refit ridge, momentum, reversal, no-trade, fixed-seed random, lagged/random-past-timestamp and same-date permuted-feature controls. All use the same simulator. |
| RSI / Bollinger / Ichimoku | Full causal feature vocabulary in `indicators.py`; standalone, trend-filter, model-filter and bounded combination trials in `strategies.py`. Display offsets never enter feature rows. |
| Feature ablations | Base + RSI/Bollinger/Ichimoku/all TA screened against refit base. All fail discovery; no successful combined model triggers further confirmatory ablations. |
| Multiplicity and uncertainty | Full-family discovery date-block max-mean test, outer paired CI, disclosed IID PSR/DSR approximations; PBO estimate explicitly withheld with methodological reasons. `docs/MULTIPLE_TESTING.md`. |
| Metrics and attribution | `research/v2_results.json`: all requested return/risk/turnover/holding/win-loss/exposure/beta metrics, static sector concentration, rank-IC distribution/CI, gross rank spread, ticker/sector/year/month/regime P&L and top-winning-lot shares. Undefined cash ratios stay null. |
| Robustness and execution falsification | All 14 predeclared main scenarios, best/worst-year deletion, top-winning-lot deletion, four causal regimes; eight sector-proxy exclusions; separate all-in cost audit scales impact with other costs. No formal pass on incomplete sector data. |
| Executable timing and costs | Prior-close quantities, next-session execution, spread/commission/slippage/impact, prior ADV, cash reductions, missing-session rejection, missed/late fill scenarios. Terminal liquidation and adjusted units remain disclosed approximations. |
| Frozen candidates | `freeze_candidate` refuses missing historical/data gates; content-addressed definitions are create-once. No real challenger qualifies, so none was frozen. |
| Prospective shadow/PAPER | `shadow.py` tests immutable forecast/outcome records, cadence, freshness, dependency hashes, duplicate/replay refusal and minimum observed evidence. Actual parallel challenger evaluation is NOT ELIGIBLE after failed gates; no synthetic history is substituted. |
| Broker safety | Original complete test suite passes. Exact PAPER account, no live fallback, stale forecast, duplicate/replay, reconciliation and currency safeguards remain unchanged. No broker calls made. |
| Reproducibility | Archived source/config and raw data hashes reproduce five fits and both 251-day 2018 comparator curves to <1e-16 daily-return discrepancy. |
| Safe publication | Only source, tests, docs, preregistration and compact research evidence are staged. Private config, account IDs, state, downloads, runs and backups stay ignored. |

## Limits that remain

An unbiased historical constituent/security-master and complete dated action/
sector dataset have not been recovered. Their effect on strategy returns cannot
be inferred from the surviving 85 names. The opening-price, impact and terminal-
liquidity models are assumptions requiring observed fills. No amount of software
testing or post-hoc sector grouping resolves these gaps. These limitations are
reasons to reject promotion, not permission to silently repair the universe or
search for a new winner.

The existing champion has one immutable forecast and zero evaluated model
outcomes at this audit. Its historical status is preserved; this audit does not
endorse its causal edge. No candidate can claim 126 prospective sessions, 25
rebalances or credible PAPER fills before those observations exist.
