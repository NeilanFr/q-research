# Working model report

Completed: 2026-09-22T01:53:17.860515+00:00

**Selected: AGGRESSIVE_V1 - ridge10, top 10 stocks, equal weighting, rebalance every five NYSE sessions. Allocation: 100% aggressive book, 0% overnight. Portfolio target is 99.5% stocks and 0.5% cash; no leverage or shorts.**

COMPETITION_V1 version: `cbe43010220707651123`. The old sector equal-weight execution policy is suspended. The final audit passed; model search and fitting have stopped.

## Evidence and selection

Train: 2013-04-01-2017-12-31, with labels purged at the boundary. Reused validation: 2018-2022. One-time final holdout: 2023-01-01-2026-09-18. All partitions are chronological. The tree used no random early-stopping split.

95,928 training labels; ten-session next-open-to-open cross-sectional excess-return target. Ridge penalty 0.03, intercept unpenalized. The alternative boosting model has 100 iterations, 7 leaves, depth 3, learning rate 0.035, minimum leaf size 250, L2 10 and fixed seed 20260921. The frozen coefficients and all 85 stock identities are in [AGGRESSIVE_V1](config/model_v1/AGGRESSIVE_V1.json).

Features: momentum, acceleration, reversal, residual_momentum, breakout, volume_momentum, volatility, volume_anomaly, gap_continuation, gap_20, vol_ratio, momentum_reversal, breakout_volume, momentum_lowvol, market_trend, trend_reversal. All use information through the feature-session close. Eligibility requires at least 200 prior sessions, raw price at least $5 and prior 20-session dollar ADV at least $50 million. Trade participation is capped at 1% of prior ADV; limited trades leave cash or residual holdings.

| Model | Validation CAGR | 10 bps CAGR | Mean 20-session return | P(20-session >10%) | Max drawdown |
|---|---:|---:|---:|---:|---:|
| old_sector_baseline | 10.10% | 9.99% | 0.90% | 2.58% | -36.71% |
| stock_equal | 11.27% | 11.16% | 0.98% | 3.63% | -32.23% |
| momentum | 4.83% | 3.28% | 0.50% | 4.03% | -38.72% |
| quality_rule | 7.64% | 5.96% | 0.68% | 2.90% | -29.80% |
| boost10 | 13.48% | 10.37% | 1.36% | 12.82% | -45.03% |
| ridge10 | 16.84% | 13.49% | 1.62% | 12.10% | -44.03% |
| ridge10_top10_inverse_vol | 16.47% | 12.91% | 1.58% | 10.40% | -44.14% |
| ridge10_top5_equal | 8.92% | 5.22% | 1.16% | 13.23% | -51.23% |
| ridge10_top3_equal | 2.93% | -0.91% | 0.93% | 14.44% | -64.12% |
| combined_50_50 | 9.70% | 7.58% | 0.87% | 3.71% | -28.49% |

Top 3 and top 5 reduced net performance. Ridge top 10 passed the frozen validation comparisons against stock equal weight and momentum at both cost levels, positive incremental returns in three of five years, leave-best-year and leave-best-stock checks, and the 20-session criterion. Its largest stock share of positive attributed P&L was 8.68%.

The ten-session rebalance stability diagnostic had 9.64% CAGR and 7.94% at stress, although its mean 20-session return remained above the stock reference. Five-session cadence matters. Inverse-volatility sizing was close to equal sizing and was not selected.

## Overnight decision and portfolio comparison

**Overnight rejected.** Three stock-level alternatives were evaluated: selective ridge ranking, weekly selective ridge ranking and momentum/regime-conditioned selection. None produced exposure above the frozen 25 bp gross prediction threshold against a 20 bp stress round trip. Their validation books remained cash. This is insufficient evidence of a tradeable edge, rather than a measured profitable overnight result. Earlier unconditional ETF overnight failures remain recorded.

The correct label is open(t+1)/close(t)-1. To avoid same-close lookahead, the researched orders entering close(t) use features from close(t-1). No overnight orders are activated.

Portfolio A (old sector baseline) had 10.10% validation CAGR; B (aggressive only) 16.84%; C (selective overnight only) 0%; D (initial 50/50 aggressive/cash books) 9.70%. D had 7.58% stress CAGR and only 3.71% probability of exceeding +10% over 20 sessions. B was selected; cash in the rejected sleeve is allocated to the aggressive book.

## One-time final holdout

Exact finalist freeze: 2026-09-21T22:04:00.988380+00:00. Audit opening: 2026-09-21T22:05:31.343724+00:00. The experiment ledger contains exactly one holdout-opening event. No alternative was selected after viewing these results.

| Model | Holdout CAGR | 10 bps CAGR | Mean 20-session return | Max drawdown |
|---|---:|---:|---:|---:|
| old_sector_baseline | 14.92% | 14.80% | 1.18% | -15.55% |
| stock_equal | 24.38% | 24.24% | 1.79% | -18.48% |
| momentum | 27.79% | 25.95% | 2.23% | -31.06% |
| ridge10 | 46.98% | 42.84% | 3.18% | -23.22% |

## Competition-length outcomes for the selected model

All windows contain 20 contiguous sessions; adjacent windows overlap. These are descriptive historical frequencies, not calibrated probabilities for the next month.

| Statistic | Validation | Final holdout |
|---|---:|---:|
| Mean | 1.62% | 3.18% |
| Median | 1.59% | 3.17% |
| 75th percentile | 6.29% | 7.54% |
| 90th percentile | 11.33% | 11.74% |
| 95th percentile | 15.31% | 16.17% |
| P(> +5%) | 30.08% | 38.05% |
| P(> +10%) | 12.10% | 14.80% |
| P(> +20%) | 1.69% | 2.41% |
| P(< -5%) | 17.26% | 12.83% |
| P(< -10%) | 6.53% | 2.19% |
| Worst 20-session return | -44.03% | -16.45% |
| Full-period max drawdown | -44.03% | -23.22% |
| Annual modeled cost at 5bps/side | 2.92% | 2.86% |
| Largest post-rebalance weight | 13.71% | 13.19% |
| Annual two-way turnover / NAV | 58.37 | 57.23 |
| Beta to SPY | 1.35 | 1.53 |

Risk/evidence limits: validation rank IC was -0.0126; the model favors volatility and reversal exposure. Beta was 1.35 in validation and 1.53 in holdout. Validation drawdown reached 44.03%; 2018 and 2022 underperformed stock equal weight. Factor-adjusted alpha has not been established. The fixed historical stock roster has availability/survivorship bias, and validation was reused. Adjusted Yahoo prices are revised total-return proxies. These limits persist despite the holdout pass.

## Current prospective targets

Batch `model_3de7b630030340cd` was frozen at 2026-09-22T01:50:11.589605+00:00 from completed September 21 bars, immutable snapshot `bd624da16839485e2e6f`. Intended entry: **September 22, 2026, 09:30 New York / 06:30 Vancouver**. Next scheduled rebalance: September 29 opening. The broker submission window is 09:10-before 09:25 New York (06:10-before 06:25 Vancouver), allowing paced what-if previews.

| Symbol | Target weight |
|---|---:|
| ADBE | 9.95% |
| BB | 9.95% |
| CHRW | 9.95% |
| CTSH | 9.95% |
| GRMN | 9.95% |
| IBM | 9.95% |
| INFY | 9.95% |
| MSFT | 9.95% |
| NFLX | 9.95% |
| ORCL | 9.95% |
| Cash | 0.50% |

Weights are executable policy inputs, not submitted orders. Share quantities require actual account NAV/cash/positions, resolved IB contracts and fresh live quotes. Reference closes are never order prices. Opening-limit nonfills and cash-constrained buys can leave material deviations from research targets; backtests assume opening execution, and this difference still needs broker commissioning. Forecasts cannot be executed after their opening session or replayed.

## Generic PAPER integration and verification

`quantlab.competition` registers immutable model policies, forecasts and shadow targets. `quantlab.paper` consumes arbitrary frozen symbols/weights and sizes whole-share position deltas; `quantlab.tws` retains the official SDK transport. Policy and model hashes are checked against the ledger. Account allowlist, explicit PAPER/enabled flags, stale forecasts/quotes, contract ambiguity, cost/cash/ADV limits, what-if previews, order idempotency, partial fills, reconnect reconciliation and durable lifecycle records remain enforced. The legacy sector policy cannot satisfy the active model version.

**129 complete-suite tests passed.** Test attestation: 2026-09-22T01:50:17.070472+00:00. Generic integration tests cover variable symbols/weights, cash targets, exiting omitted holdings, ADV capacity, rejected shadows and the five-session schedule. Original broker tests cover live/wrong-account rejection, disabled execution, replay/restart, partial fills, quote freshness and prediction/order/fill separation. Tests use isolated synthetic broker fixtures; no real broker validation or fill is claimed.

Actual commissioning is blocked: no independently configured paper allowlist; no TWS/IB Gateway listener on the conventional local ports. Official SDK 10.50.2 is installed. No account, balances, real-time entitlements, contracts, broker what-if response or fills have been observed. No orders have been transmitted.

## Exact next command

First provide the independently verified paper account ID and actual socket port in ignored `config/private_paper.json`, using `config/paper_connection.example.json`; leave execution disabled. In TWS Paper Trading, enable **Global Configuration > API > Settings > Enable ActiveX and Socket Clients**, with **Read-Only API checked** for the initial observation.

```powershell
.\.venv\Scripts\python.exe -m quantlab.paper_cli inspect
```

After successful account inspection, the command below performs the current dry run, paced exact-order what-if checks and enables PAPER only after all gates pass. It must start inside the scheduled pre-open window; the TWS Read-Only API setting must then permit paper orders.

```powershell
.\.venv\Scripts\python.exe -m quantlab.paper_cli execute --enable-paper --batch model_3de7b630030340cd
```

Persistent scheduler (initially without any broker access):

```powershell
.\.venv\Scripts\python.exe -m quantlab.paper_cli watch --research-only
```

For future scheduled forecasts, `python -m quantlab.competition fetch`, then `evaluate`, then `predict`. Prediction refuses non-rebalance dates and duplicates. A commissioned `paper_cli watch` observes accounts and executes only the frozen schedule while the account flag remains enabled. Keep Windows/TWS available; no OS startup task has been installed. Confirm the competition terminal marking/liquidation date before the final holding window.

## Frozen artifacts

- [COMPETITION_V1](config/model_v1/COMPETITION_V1.json), [AGGRESSIVE_V1](config/model_v1/AGGRESSIVE_V1.json), [overnight rejection](config/model_v1/OVERNIGHT_REJECTED.json).
- [Prospective targets](state/model_forward/model_3de7b630030340cd/targets.csv), [immutable forecast](state/model_forward/model_3de7b630030340cd/forecast.json).
- [Validation summary](runs/20260921T210708_working_model/validation_summary.csv), [final holdout summary](runs/20260921T210708_working_model/holdout_summary.csv).
- [Finalist freeze](runs/20260921T210708_working_model/FINALIST_FROZEN.json), [final decision](runs/20260921T210708_working_model/FINAL_DECISION.json).
- [Tests](state/paper_checks/latest-tests.txt), [resume status](WORKING_MODEL_STATUS.md).

Reproduce the model environment with `python -m pip install -r requirements-model.lock`; install IBKR separately from its official SDK archive. Research predictions use the already frozen coefficients and do not refit.
