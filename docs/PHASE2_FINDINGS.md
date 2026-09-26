# Phase 2 findings — September 21, 2026

**No challenger earns promotion.** The broader screen works, but the requested
barbell does not beat the simple stock control under the declared assumptions.
The AI follow-up failed. Capital time-sharing is rejected for these rules.
No orders were submitted. **84 tests pass.**

[Full report](../runs/20260921T174553_phase2_a3bc02/report.md) ·
[Ranked table](../runs/20260921T174553_phase2_a3bc02/aggressive_summary.csv) ·
[Portfolio comparison](../runs/20260921T174553_phase2_a3bc02/portfolio/validation/comparison.csv)

## Evidence and universe

The dated 2012 QQQ/DIA roster contains 127 historical companies. The snapshot
supports 85 continuous stock histories plus 13 ETFs. All 42 stock exclusions
remain in the [audit](../runs/20260921T174553_phase2_a3bc02/universe_audit.csv).
Missing delisted companies, unresolved successors and vendor availability create
survivor/availability bias; even the stock equal-weight control is affected.
This is exploratory screening, not an unbiased historical equity reconstruction.
The historical coverage gate uses 2010–2022 only, but the earlier download step
also required currently retrievable/valid histories, adding availability bias.
No missing prices or delisting outcomes were fabricated.

Discovery used April 2013–2017; validation reused 2018–2022. Historical 2023+
outcomes remain unscored. The first discovery run,
`20260921T173042_phase2_36e614`, froze momentum top10 and QQQ pullback overnight
before validation. Attempt `20260921T173413_phase2_48b1f6` stopped at a liquidity
rejection. Completed run `20260921T174553_phase2_a3bc02` retained that limit and
recorded failed candidates while finishing the others. This continuation is not
a fresh test. Source, configs, data hashes and failed attempts remain archived.

## Results after 5 bps per traded dollar on each leg

| Validation strategy | Net annualized | Max drawdown | Mean 20 sessions | 90th percentile |
|---|---:|---:|---:|---:|
| Stock equal weight | 11.39% | -32.06% | 1.00% | 6.78% |
| Volume/price interaction | 12.35% | -33.12% | 1.09% | 8.82% |
| Momentum top10 | 9.25% | -38.39% | 0.85% | 7.29% |
| Momentum top5 | 3.95% | -36.17% | 0.51% | 7.96% |
| AI momentum/pullback | 4.40% | -37.12% | 0.52% | 7.36% |
| Selected overnight rule | -3.21% | -24.38% | -0.17% | 2.93% |
| Initial 50/50 allocation | 3.77% | -32.99% | 0.39% | 4.92% |
| SPY buy-and-hold | 9.14% | -33.72% | 0.83% | 6.14% |
| Sector equal weight | 10.13% | -36.76% | 0.91% | 6.09% |

Overlapping 20-session windows describe history, not expected competition
returns. The full table contains medians, 75th/95th percentiles, +5/+10/+20%
and -5/-10% frequencies, window drawdowns, skew, beta, turnover, concentration,
exposure, cost stress and paired block intervals. Momentum exceeded +10% in
5.17% of windows and lost over 10% in 3.55%; equal weight's corresponding
frequencies were 3.95% and 2.50%. More upside exposure also increased losses.
Top5 weights drifted as high as 26.19% between scheduled rebalances.

The apparent volume/price advantage disappears at 10 bps: **8.40% CAGR versus
11.29% for equal weight**. Its base-cost annual mean active return was 1.54%,
with an exploratory 95% block interval of **-7.60% to +10.82%**. It traded about
71.5 times NAV annually, counting both sides, and relative results varied by
year. The formula also favors falling prices with declining volume: it is a
signed interaction, not pure momentum confirmation. Selecting this row after
viewing validation supplies no independent evidence.

Reversal and ranked ridge regression breached the fixed 1% of prior ADV limit
in FOSL on December 15, 2021: **1.4260% and 1.2125%**. Both remain failed trials.
Limits were not raised and unconstrained returns were not ranked as feasible.
Ridge's discovery results were in-sample; training purged five-session labels
that exited beyond the discovery boundary.

## Overnight findings

Raw/adjusted close-to-open and open-to-close identities reconcile with maximum
factor residual below 1e-9. Overnight accounted for about 97.4% of SPY's and
77.5% of QQQ's validation compounded log price-proxy return. That attribution
does not establish a profitable daily round-trip strategy.

Unconditional SPY/QQQ overnight gross CAGRs were 8.92%/8.96%. At 1 bp per leg
they fell to 3.57%/3.61%; at 5 bps they were **-15.34%/-15.31%**. Break-even
was about **1.70 bps per leg**. One bp is optimistic sensitivity, not a measured
fill cost. The [overnight report](../runs/20260921T174553_phase2_a3bc02/overnight/report.md)
contains all ETFs, costs, years, regimes, tails and unconditional controls.

Trend and pullback conditions were also compared with chronologically calibrated
exposure controls. Neither family earned a positive net return at 5 bps.
The discovery-selected QQQ three-session-pullback rule returned -3.21% in
validation, with break-even around 3.48 bps. Some conditional improvements over
controls reflect different exposure and do not overcome net losses or search
uncertainty. Overnight is not established as a safe or profitable sleeve.

## AI discovery loop and falsification

Actual discovery contributions showed momentum's gains came overwhelmingly
from overnight ownership. A four-cell residual diagnostic found top-quintile
momentum stocks after a five-day loss had about +0.114% mean five-session excess
returns, versus -0.024% without that loss. Labels overlapped and were averaged
by date; this was hypothesis generation, not significance testing.

The [registered hypothesis](../config/phase2_followup.json) converted that
observation into positive medium-term momentum plus equal momentum/reversal
ranks, held top10. Mechanism, variables, comparator, provenance and falsification
were recorded before validation. It returned **4.40% versus momentum's 9.25%**,
and only 0.47% at 10 bps. It underperformed equal weight in four of five validation
years. Reject this implementation: the conditional average did not translate
into a useful portfolio ranking. These experiments do not establish AI alpha.

## Separate sleeve attribution and time-sharing

Each $500k sleeve compounds independently; no daily 50/50 reset occurs.
[Dollar attribution](../runs/20260921T174553_phase2_a3bc02/portfolio/validation/auction_attribution/daily_dollars_and_exposure.csv)
and adjacent files contain exact modeled holdings, starting/ending dollars,
overnight/intraday P&L, fees, contribution, drawdown and auction exposures.
Reconciliation error is below $0.000001. Fractional-share adjusted-price marks
do not establish executable prices, intraday extremes, or ETF look-through risk.

Selling stocks every close removes their overnight contribution and adds daily
round trips. Time-sharing returned **-27.64% annualized at 5 bps per leg** and
**-49.49% at 10 bps**. Reject it for these signals. Cost stress does not prove
simultaneous fills, auction availability, funding or executable capital reuse.

## Forward decision

The original sector competition policy and Monday opening forecast are unchanged.
The separate [Phase 2 policy](../config/phase2_shadows_v1.json) is **shadow only**,
with stock equal weight and cash references. Momentum, the validation-selected
volume interaction, top5 concentration and the rejected AI rule are tracked
as challengers. Historically infeasible reversal/ridge are excluded.

The first [overnight shadow](../state/phase2_forward/phase2_3f9f923bad594764/forecast.json)
was frozen before September 21's close, using September 18 features for September
21 close → September 22 open. Its outcome is pending. Monday's already-past
stock opening opportunity was not backfilled. Stock targets are prepared for
September 22 open, then every five sessions, using fresh completed data; see
[README commands](../README.md). Late/stale/duplicate/modified forecasts are
rejected and first-observed outcomes remain frozen.

The evaluator scores isolated costed prediction batches, distinct from a funded
continuous portfolio or broker fills. Cash yield is zero; costs, spreads,
auction capacity, corporate-action cash flows and actual IBKR execution remain
unverified. No paper account, broker listener or scheduler is configured.
Contest finish/marking rules and shorting/leverage permissions remain unknown.
Next priorities are prospective targets, measured paper fills once configured,
and better historical delisting/corporate-action data. Do not infer that the
historical best row predicts the contest winner or that higher leverage creates
predictive value.
