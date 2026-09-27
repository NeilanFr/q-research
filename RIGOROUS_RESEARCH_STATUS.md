# Rigorous research status

Updated September 27, 2026. **No challenger promoted. COMPETITION_V1 / ridge10
remains unchanged. No live or PAPER orders were sent.**

| Required status | Result |
|---|---|
| CURRENT CHAMPION | COMPETITION_V1 / ridge10, version `cbe43010220707651123`; frozen source, config and artifacts intact. |
| CHALLENGERS | 20 predeclared RSI, Bollinger, Ichimoku, technical/ridge combinations and feature ablations. No frozen challenger. |
| HYPOTHESES TESTED | Five RSI, four Bollinger, two Ichimoku, five rule/model combinations and four learned feature-block ablations. |
| TRIAL COUNT | 32 primary strategies (20 challengers, eight references, four controls); 362 main simulations; 120 sector-proxy exclusions; 30 all-in cost stresses; two archived-source replications: **514 simulations total**. Prior ledger: 378 complete, three failed, six unrun trial rows. |
| SEARCH BUDGET | 20 primary challengers; three RSI periods, one initial Bollinger set and one initial Ichimoku set; one predeclared joint perturbation, never selectable. Nine primary combinations/ablations. No expanded strategy search. |
| LEAKAGE BUGS FOUND | Backward availability screening creates survivorship bias; 2026 observations cannot certify old-row availability; legacy exact target sizing uses opening NAV; stale README sealing claim corrected. Deliberate future-return, Ichimoku, normalization, timing and membership bugs are detected by tests. |
| RESULTS INVALIDATED | Unbiased point-in-time alpha/promotion interpretation of survivor-only revised data is INVALID. Exact executable opening-share interpretation of legacy target-weight fills is INVALID. Original numerical records remain intact. |
| SURVIVORSHIP LIMITATIONS | 85/127 historical identities retained; 42 excluded (33.07%). Delistings, bankruptcies, mergers, reused symbols and incomplete histories remain explicit. Return-bias magnitude is not identifiable. |
| RSI RESULT | All five fail discovery. RSI14/21 reversal improve on momentum by only 0.66/0.86 annual mean percentage points, below the fixed 1-point gate. No relaxed thresholds. |
| BOLLINGER RESULT | Mean reversion passes the economic screen; fails validation uncertainty/multiplicity. Outer CAGR 19.92%, Sharpe 0.843, max drawdown −32.31%, on biased exploratory data. Mean excess vs matched champion 10.00 points; CI −4.12 to +24.09; p=0.0689. |
| ICHIMOKU RESULT | Both standalone/trend variants and restrained technical combinations fail discovery. Causal 9/26/52 computation and explicit displacement leakage tests pass. |
| BEST COMBINED RESULT | Base + all TA has the highest combined screening CAGR, 14.26%, versus refit base 11.11%, but fails the fixed baseline gate. No reliable incremental TA evidence. |
| ROBUSTNESS RESULT | Discovery survivor stays positive in declared cost/delay/impact/holding-count stresses and eight static sector-proxy exclusions. At 3x all-in costs mean excess vs equal weight falls to 0.33 points. Rebalance timing/regime dependence persists; top 1% of closed FIFO fragments contribute 60.52% of net P&L. No formal robustness/promotion pass on missing PIT data and failed validation. |
| PROSPECTIVE STATUS | No challenger is eligible for freeze or parallel prospective testing. Existing champion: one immutable forecast, zero evaluated model outcomes; next scheduled rebalance requires completed September 28 bars. New shadow infrastructure is tested on synthetic fixtures only. |
| PROMOTION STATUS | NONE. Point-in-time data gate false; statistical validation failed; no prospective challenger record. Champion retention does not establish its causal edge. |

The main run is `runs/V2_TA_20260927_01_attempt1`; large artifacts remain local.
[Compact results](research/v2_results.json), [append-only ledger](research/v2_ledger.jsonl),
[full technical report](docs/TECHNICAL_INDICATOR_RESEARCH.md), and
[requirement-by-requirement audit](docs/BACKTEST_AUDIT.md) provide the evidence.

The 2023-01-01 through 2026-09-18 holdout was permanently opened on September 21,
2026. Reused 2018-2022 validation is not fresh confirmation. Current V2 source
never rescored the opened holdout. Old archived logs are historical records.

Verification: **189 full-suite tests passed** (including the original 164 tests).
Archived-source reproduction verified all five 2018
fits and both 251-session champion/Bollinger curves with <1e-16 daily-return
difference. The [verification attestation](research/v2_verification.json) records
the exact current test totals, tested source and champion hashes.

The completed study supports a negative promotion decision. A new experiment
requires a new registration; it must not silently reuse this study's failed
hypotheses or treat the same historical periods as sealed. Point-in-time security,
corporate-action and sector histories and genuinely prospective observations are
the evidence gaps, not additional parameter search.
