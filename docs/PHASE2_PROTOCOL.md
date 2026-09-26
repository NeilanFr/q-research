# Phase 2 protocol, before new strategy results

Retain the existing data cache, experiment ledger, accounting functions, safety
boundary and frozen Monday sector forecasts. Extend only the research functions
needed for stock cross-sections, session decomposition and sleeve attribution.

Use the SEC-filed September 2012 QQQ and October 2012 DIA schedules as a fixed
historical roster, public in January/February 2013. First research decision is
April 2013. Every historical identity and missing/unusable series is recorded.
Public Yahoo coverage in 2026 may omit delisted companies; results on available
survivors are **not an unbiased reconstruction**. Avoid reusing a modern ticker
for an unrelated predecessor. No fabricated delisting returns or silent fills.
No paid-data credentials are configured. Broader research is useful for screening
despite this limitation; the limitation constrains promotion claims.

Discovery: April 2013–2017. Validation: 2018–2022, already exposed in phase 1
for ETFs and now explicitly reused. 2023+ remains sealed for historical scoring.
Nothing in this protocol authorizes opening that holdout.

Overnight means adjusted open(t)/adjusted close(t-1)-1; intraday means
close(t)/open(t)-1. Both raw and adjusted multiplicative identities must reconcile.
Adjusted overnight includes the source's corporate-action normalization, not an
exact cash/share ledger. Report raw-price gaps separately. Never rename an
open-to-open return overnight. Daily-only conditional orders entering close(t)
use features through close(t-1), since final close(t) information is unavailable
at closing-auction submission deadlines. No after-hours features without actual
timestamped data.

First unconditional exposures: SPY, QQQ, IWM, DIA and nine sector ETFs. Conditional
rules: prior-day price above its 200-session mean; prior-day three-session loss.
Include an exposure-matched control calibrated on earlier data. Costs are per
leg; display 0/1/2.5/5/10 bps and break-even assumptions. One bp is an optimistic
scenario, not a measured fill. The baseline comparison remains 5 bps.

Aggressive first screen: equal weight; 63-session momentum excluding latest five;
21-vs-63-session acceleration; 63-session breakout proximity; volume-confirmed
momentum; five-session reversal; gap continuation; market-residual momentum;
volatility contraction within an uptrend; and a fixed-penalty ranked linear
regression. Predict future five-session excess returns; purge labels past each
training boundary. Regression trains only on discovery before validation.
No neural network or event/news features without suitable historical inputs.

Use prior-close information to rebalance at the next open every five sessions,
with close-marked daily P&L and separate overnight/intraday contributions.
Start top10 equal allocations. At most one discovery-selected rule gets a top5
expression check, with the same simple top5 momentum control. No top-k grid,
leverage or shorts. Competition permissions remain unknown.

Report 20-session contiguous-window distributions, tails, drawdowns, beta,
turnover, concentration, yearly/regime consistency and cost stress. Overlapping
windows are descriptive, not independent trials. Record all attempted rules and
the discovery-selected concentration rule before looking at its validation.
After inspecting actual discovery residuals, register at least one explicit AI
follow-up, including thesis, variables, mechanism and falsification; label its
reused validation as adaptive. This is a research loop, not an order-generating LLM.

Combine independent sleeves at $500k each without daily capital rebalancing;
retain every sleeve's dollars, exposure, holdings and P&L. Compare SPY, sector
equal weight, stock equal weight, overnight-only, aggressive-only and combined.
Time-sharing is a separate diagnostic using intraday-only aggressive returns
and overnight exposure; require more costs for non-simultaneous fills and do not
claim an auction-perfect simulation establishes executable capital reuse.

Forward versions are candidates with explicit evidence labels. No old forecast
will be overwritten. If neither sleeve has a credible net edge, keep that fact
visible and freeze useful controls/shadows instead of declaring alpha.

## Resumption: executable selection and reporting rules

The first resumed run scores discovery only. Among the unfitted top10 rules,
select the highest mean contiguous 20-session net return for the single top5
expression check; exclude the discovery-fitted ridge from that selection.
Overnight selection compares SPY/QQQ unconditional, prior-day trend, and
prior-day pullback at the baseline 5bps per leg, using discovery net CAGR.
These selections identify challengers for harder tests; they are not promotion.
Write selections before running validation and retain the parent discovery ID.
The 20-session distributions overlap and are descriptive. Neither that ranking
nor an unadjusted block interval overcomes missing historical stock identities.

Stock eligibility requires full 2010-2022 session coverage on the historical
roster. This deliberately restrictive public-data compromise excludes companies
with unavailable delisting histories; all 127 roster identities remain in the
audit. Coverage after 2022 is not used for historical inclusion. Apply the
20-million-dollar rolling liquidity floor using only prior observations.
Reject opening or closing trades above 1% of prior ADV instead of assuming fills.

Both sleeves report the same exit-session calendar, omitting each partition's
first session so the first overnight entry close is inside the partition.
Aggressive holdings earn their gaps before the opening rebalance; the final
close liquidates the sleeve. Stocks and ETFs use adjusted-price proxies.
Time-sharing retains the five-session ranking cadence but sells every close
and re-enters every open. Report it at both 5 and 10bps per traded leg. Without
intraday quote/fill data it remains an auction-price diagnostic.
