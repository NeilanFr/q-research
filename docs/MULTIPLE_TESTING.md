# Multiple testing

The V2 search is fixed in `config/rigorous_v2.json` before its outcomes are run.
Twenty challenger specifications cover RSI, Bollinger, Ichimoku, restrained
combinations and four feature-block ablations. Eight references and four negative
controls also consume strategy evaluations. All failures count. Operational
attempts, unique hypotheses, fold evaluations and stress scenarios are distinct
counts; repeated annual folds are not independent strategy discoveries.

The prior ledger contains 378 completed, three failed and six not-run trial rows
across seven runs. These include repeated partitions and correlated variants.
They are neither zero prior search nor 387 independent bets. Unrecorded researcher
choices are unknown. The current study cannot recover an unbiased confirmatory
p-value from this history.

Date-block bootstrap intervals use 20-session circular blocks and a fixed seed.
Every strategy receives the same resampled dates, preserving cross-strategy and
within-block dependence. Discovery excess is measured against liquid-stock equal
weight; outer excess against the frozen champion. A maximum recentered mean
statistic across candidates supplies a White Reality Check-style familywise
diagnostic. It is an unstudentized max-mean bootstrap, **not** an implementation of
Hansen's SPA. Stationarity, block length, finite sample size, repeated validation
and missing strategies limit interpretation. Negative-performing hypotheses stay
in the discovery family. Survivor-only outer inference cannot account for every
historical researcher choice; promotion remains blocked on reused biased data.

Probabilistic and Deflated Sharpe diagnostics use daily Sharpe, sample skewness
and kurtosis, the dispersion of candidate Sharpes, and a disclosed trial-count
sensitivity that includes prior ledger rows. Their IID approximation does not
correct serial dependence or make the prior search count known. These numbers
are exploratory descriptors, never automatic evidence of significance. The
[original Deflated Sharpe paper](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
explains the selection and non-normality corrections.

Probability of Backtest Overfitting is not reported as a numerical estimate here.
Combinatorial symmetric cross-validation would recycle these already reused
periods and does not replace chronological outer evaluation. With a gated,
incomplete candidate-by-outer-return matrix, a single PBO number would obscure
the experiment design. Inner winners and their subsequent outer performance are
preserved to expose selection instability. See the
[original PBO paper](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
