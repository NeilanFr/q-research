# Technical indicator research — V2

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
`runs/V2_TA_20260927_01_attempt1`.

## Design and search budget

The registered study is `V2_TA_20260927_01`. Discovery consists of independently
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
| rsi7_reversal | RSI | 16.73% | 1.060 | -21.89% | Discovery FAIL; validation not run |
| rsi14_reversal | RSI | 18.18% | 1.135 | -19.16% | Discovery FAIL; validation not run |
| rsi21_reversal | RSI | 18.40% | 1.141 | -20.01% | Discovery FAIL; validation not run |
| rsi14_trend | RSI | 10.79% | 0.756 | -17.28% | Discovery FAIL; validation not run |
| rsi14_continuation | RSI | 5.41% | 0.503 | -23.54% | Discovery FAIL; validation not run |
| bb_reversal | BOLLINGER | 20.41% | 1.232 | -22.11% | Discovery PASS; validation FAIL |
| bb_trend_reversal | BOLLINGER | 9.58% | 0.686 | -20.07% | Discovery FAIL; validation not run |
| bb_breakout | BOLLINGER | 2.73% | 0.275 | -22.39% | Discovery FAIL; validation not run |
| bb_squeeze | BOLLINGER | -6.55% | -0.657 | -21.55% | Discovery FAIL; validation not run |
| ichimoku | ICHIMOKU | 7.91% | 0.648 | -15.61% | Discovery FAIL; validation not run |
| ichimoku_market | ICHIMOKU | 7.14% | 0.690 | -16.30% | Discovery FAIL; validation not run |
| ichimoku_rsi | COMBINED | 1.49% | 0.311 | -10.76% | Discovery FAIL; validation not run |
| bb_ichimoku | COMBINED | 3.86% | 0.365 | -16.58% | Discovery FAIL; validation not run |
| ridge_rsi_veto | COMBINED | 11.64% | 0.699 | -24.86% | Discovery FAIL; validation not run |
| ridge_ichimoku_veto | COMBINED | 5.97% | 0.450 | -18.15% | Discovery FAIL; validation not run |
| ridge_bb_veto | COMBINED | 9.85% | 0.610 | -31.20% | Discovery FAIL; validation not run |
| base_plus_rsi | ABLATION | 13.09% | 0.767 | -27.42% | Discovery FAIL; validation not run |
| base_plus_bb | ABLATION | 7.63% | 0.495 | -28.97% | Discovery FAIL; validation not run |
| base_plus_ichimoku | ABLATION | 9.74% | 0.611 | -24.00% | Discovery FAIL; validation not run |
| base_plus_all | ABLATION | 14.26% | 0.840 | -27.76% | Discovery FAIL; validation not run |

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
| bb_reversal | 19.92% | 0.843 | -32.31% | 72.59 |
| champion | 6.12% | 0.346 | -48.22% | 59.71 |
| refit_ridge | 15.56% | 0.609 | -43.06% | 69.02 |
| stock_equal | 11.02% | 0.579 | -32.31% | 3.70 |
| spy_buy_hold | 9.11% | 0.513 | -33.72% | 1.99 |
| momentum | 9.85% | 0.507 | -31.65% | 31.02 |
| reversal | 18.35% | 0.716 | -38.91% | 89.01 |
| random | 11.52% | 0.571 | -32.81% | 90.71 |
| permuted_features | 13.09% | 0.630 | -32.15% | 89.64 |
| randomized_timestamps | 6.87% | 0.367 | -39.66% | 63.45 |
| ridge_delay1 | 14.60% | 0.584 | -46.13% | 68.44 |
| ridge_delay5 | -7.24% | -0.090 | -50.49% | 68.82 |
| no_trade | 0.00% | n/a | 0.00% | 0.00 |

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
| cost_2x | 9.37% |
| cost_3x | 8.73% |
| extra_slippage_5bps | 9.37% |
| delay_1 | 8.79% |
| delay_3 | 12.70% |
| alternate_rebalance | 3.94% |
| cadence_10 | 11.79% |
| miss_every_fifth | 9.20% |
| missing_open_auction | 10.84% |
| impact_3x | 9.75% |
| top5 | 12.08% |
| top20 | 5.13% |
| parameter_perturbation | 10.00% |
| later_start_2020 | 6.53% |
| remove_best_year | 7.56% |
| remove_worst_year | 11.17% |
| high_vol | 3.34% |
| low_vol | 19.11% |
| bull | 11.57% |
| bear | 5.59% |

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
| Consumer discretionary | 13 | 2.10% | 2.61% |
| Consumer staples | 6 | 9.38% | 7.65% |
| Energy | 2 | 8.95% | 7.23% |
| Financials | 4 | 9.53% | 6.72% |
| Health care | 11 | 14.94% | 10.96% |
| Industrials | 8 | 10.56% | 10.40% |
| Media and telecom | 6 | 10.16% | 7.07% |
| Technology | 35 | 9.31% | 7.74% |

## Winner concentration and complete survivor metrics

Trades here are closed FIFO lot fragments, including partial rebalances. There
are 4,353 nonzero fill records and 2,550 closed fragments in outer evaluation;
these are not 2,550 independent bets. Average holding time is adjusted-unit
weighted across those fragments. Metrics of winners/losers use net lot returns.

| Largest winning fragments | Count | Fraction of cumulative net P&L | Fraction of positive P&L |
|---|---:|---:|---:|
| top_1 | 1 | 3.93% | 0.79% |
| top_10pct | 255 | 283.48% | 57.31% |
| top_1pct | 26 | 60.52% | 12.24% |
| top_3 | 3 | 11.11% | 2.25% |
| top_5pct | 128 | 185.75% | 37.55% |

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
| annual_consistency | 0.800 |
| annual_turnover | 72.586 |
| annualized_volatility | 0.254 |
| average_holding_period | 6.913 |
| average_loser | -0.039 |
| average_winner | 0.039 |
| beta | 1.046 |
| cagr | 0.199 |
| calmar | 0.616 |
| exposure | 0.995 |
| fill_count | 4353.000 |
| hit_rate | 0.562 |
| max_drawdown | -0.323 |
| monthly_consistency | 0.633 |
| profit_factor | 1.253 |
| sector_classification_status | static_analytical_groups_NOT_point_in_time |
| sector_concentration | 0.995 |
| sharpe | 0.843 |
| sortino | 1.226 |
| tail_loss_5pct | -0.036 |
| total_return | 1.478 |
| trade_count | 2550.000 |

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
