# Evidence at the first milestone

Current update: the new ridge10 model passed its frozen one-time final audit;
the overnight sleeve was rejected. See [WORKING_MODEL_REPORT.md](../WORKING_MODEL_REPORT.md)
for the current evidence, risks and prospective portfolio. Findings below remain
the original milestone record, not the currently selected execution policy.

The system works end to end on real data. It has not established that AI adds
predictive value. No broker position, submission or fill has occurred.

## First prespecified screen

Data: immutable Yahoo snapshot `e9c58c170b0b65d53e00`, 5,462 complete sessions
for each of nine sector SPDR ETFs plus SPY. Experiments score only 2006–2022.
First run: `20260920T151942_ca864aa8`; 3,018 discovery and 1,257 validation
one-session decisions, common to all models. These are counts of dates, not
independent ETF observations. All nine configurations were registered before
their results were calculated.

Selected validation results, 2018–2022:

| Model | Origin | Net CAGR | Annual paired excess vs equal weight | Exploratory 95% block interval |
|---|---|---:|---:|---|
| Equal weight | Baseline | 10.02% | 0 | Reference |
| SPY buy-and-hold | Baseline | 9.04% | -0.84% | [-5.16%, +3.29%] |
| Momentum | Baseline | 7.46% | -2.44% | [-5.74%, +0.97%] |
| Reversal | Baseline | 6.72% | -2.79% | [-5.62%, +0.04%] |
| Low volatility | Baseline | 10.58% | +0.14% | [-3.01%, +3.14%] |
| Trend pullback | AI proposal | 8.50% | -1.37% | [-2.69%, -0.01%] |
| Volatility-scaled reversal | AI proposal | 7.30% | -2.26% | [-4.93%, +0.46%] |

Annual paired excess is 252 times mean daily net-return difference, not a CAGR
difference or regression alpha. Base costs are 5 bps per bought/sold dollar.
Intervals do not correct for selection or multiple comparisons. No promotion
follows from low volatility's slightly better CAGR: it has lower equity beta
(about 0.85 vs equal weight's 0.95) and an imprecise paired active estimate.

## Hypotheses and what their failures taught us

**AI H1: pullbacks in established relative winners recover.** Mechanism:
short-term price pressure inside a persistent sector trend. Measurement:
positive centered rank of 60-session momentum times positive centered rank of
five-session reversal. Falsification: it should improve on the additive blend
and equal weight after costs without relying on one episode.

It underperformed equal weight. It beat the additive blend by 1.96%/year net,
but 1.33 percentage points were cost savings. Ties also shrink its tilts:
validation tracking error was about 1.83%, versus 3.07% for the blend.
The control therefore does not isolate the nonlinear mechanism from active-risk
and turnover reductions. No claim of improved prediction is justified.

**AI H2: unusually large sector-relative pullbacks contain more information.**
Mechanism: standardize reversal by trailing residual volatility to distinguish
an unusual move from routine sector noise. Measurement: five-session reversal
relative to the sector mean divided by 60-session residual volatility times
sqrt(5). Falsification: improve on plain reversal and equal weight, not simply
load defensively on low-volatility sectors.

It lost to equal weight after costs. Its +0.53%/year daily net advantage over
ordinary reversal was uncertain (interval approximately -0.09% to +1.14%).
Daily IC intervals for every scored rule spanned zero. Volatility scaling does
not yet add defensible value.

IC is undefined on constant-score dates, which are excluded from its mean;
`ic_dates` records the count. This especially affects the tied interaction rule.
The five-session study's IC still describes freshly calculated daily scores,
not the drifted holdings retained between rebalances.

**A focused cost diagnostic followed, not another independent test.** Run
`20260920T152355_b81a3e5b` reuses exposed validation and trades the same rules
every five sessions. Equal weight earned 10.08% CAGR, pullback 9.46%, and
volatility-scaled reversal 9.94%. Paired incremental means were -0.54% and
+0.09%/year; both intervals include zero and both lose at 10 bps. The
pullback/blend advantage shrank to +0.08%/year. This weakens the mechanism claim
and points toward turnover, active-risk scaling and short-lived effects.

## Failure cases worth inspecting

Daily pullback lagged equal weight in four of five validation years; its relative
strength was concentrated in 2020. On the holding interval May 26–27, 2020 it
made money but trailed equal weight by about 52 bps. Absolute profit alone would
have hidden that failure. `failures.csv` links that case to weights and per-symbol
P&L. Its largest absolute losses, like the baselines', reflect substantial common
equity exposure. This is an intentionally equity-risk portfolio, not a market
neutral test.

The daily reversal rule traded about 65 times portfolio NAV annually. Its gross
incremental mean was only +0.36%/year, against incremental cost drag around
3.15%/year. Costs can explain the negative net result without proving the
underlying gross effect is stable or real. Slower turnover alone did not solve
that evidence problem.

## Skeptical review and remaining uncertainty

- Chronological splits and future-perturbation tests reduce accidental leakage.
  They cannot undo prior human/AI knowledge of famous historical patterns or
  turn vendor-restated data into true historical vintages.
- Adjusted opens approximate reinvested total returns. Opening auction fills,
  commissions, share rounding, cash dividend payment dates and market impact
  need separate prospective measurement. The cash/share ledger is not complete.
- Original sector funds avoid a current-stock membership shortcut, but exclude
  failed/other funds by construction; 2016/2018 classifications changed exposures.
  There are only nine correlated cross-sectional observations per date.
- The five-session diagnostic reused validation. No best-cadence selection,
  holdout score or automatic promotion was performed. Historical 2023+ returns
  are unscored; current prices necessarily inform prospective features and risk.
- The target cap is 20% at rebalance, not a continuous concentration guarantee.
  Drift reached 21.31% in the five-session reversal diagnostic. Buy-and-hold
  benchmarks naturally have a different concentration profile.
- Two AI-proposed rules and systematic baselines exist. There is no separate
  human-only hypothesis sample or controlled productivity study yet, so this
  cannot identify the causal contribution of AI to research performance.
- A one-month forward test can identify operational failures and incremental
  behavior, but will have low statistical power for general alpha claims.

The retained implementation is data snapshots, signal rules, accounting,
diagnostics, tests and immutable forecasts. With half the time, those are still
the components to keep. A UI, custom language, model hierarchy, neural network
or news pipeline would not have changed this first conclusion.

## Decision and next experiment

Keep daily equal weight as the active reference policy and all daily alternatives
as shadows. This is a clear hurdle, not a claim that it maximizes contest rank.
Preserve actual prospective forecasts before their opening outcomes. Do not
consume the historical holdout to rescue these candidates.

The next highest-value step is reliable paper-account connectivity, source/price
reconciliation, and the first genuine forward observations. If point-in-time
equity and corporate-action data are available, broader cross-sectional research
would increase useful observations. Otherwise, favor prespecified low-turnover
tests with credible mechanisms over more combinations of these same indicators.
