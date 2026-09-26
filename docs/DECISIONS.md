# Decision log

## 2026-09-19/20, before observing strategy results

- Empty repository; no inherited architecture. 16 logical CPUs, 51.2 GB RAM
  (17.9 GB free), 22.2 GB disk free. System Python 3.14; use an isolated 3.13
  environment with compatible wheels. The registered 3.12 installation is absent.
  No provider credential names or local market datasets found in the workspace,
  environment, or conventional user data/config locations. This was a targeted
  inspection, not a search through unrelated private files.
- Approximately 32 hours remained to the September 21 NYSE open on inspection.
  Build an ordinary Python CLI with cached files and SQLite experiment records.
  UI, natural-language compiler, deep learning, and orchestration are deferred:
  none currently improves evidence more than a functioning experiment.
- Use the nine original sector SPDR ETFs, plus SPY as a market comparator.
  They have actual tradable fund histories and avoid reconstructing a stock
  universe from today's constituents. They are correlated, not nine independent
  experiments. This is a sector-rotation pilot, not a broad equity alpha test.
  Survivor selection remains a limitation; sector definitions changed in 2016
  and 2018. [Issuer inception history](https://www.ssga.com/mainfund/XLK).
- Yahoo chart JSON worked; Stooq returned challenge HTML with status 200.
  Cache raw JSON with timestamps and hashes. Yahoo's public endpoint is
  undocumented and unsupported; no service guarantee or historical as-of vintage.
  [Yahoo adjustment explanation](https://help.yahoo.com/kb/SLN28256.html).
- Signals use split/dividend-adjusted close ratios. Main returns use adjusted
  opens, a total-return-unit proxy (reinvestment at adjustment convention, not
  an exact cash-dividend ledger). Never use these adjusted prices as order
  prices. Record this limitation in every report. Ratios avoid future scale
  leakage, but provider revisions and corporate-action errors remain possible.
- Predict next-open to following-open relative returns using data through the
  prior close. Daily targets do not overlap. Require complete exchange sessions;
  never fill missing bars or align returns on an inner join silently.
- Predeclare discovery 2006–2017 and validation 2018–2022; purge decisions whose
  exits fall outside their partition. Reserve 2023 onward from historical
  performance inspection. These cutoffs were chosen before running results.
  All strategies are deterministic rules; no training or parameter search.
  The future competition is stronger evidence than this historical split.
- Compare equal weight, equal-weight buy-and-hold, SPY buy-and-hold, momentum,
  reversal, low volatility and an additive momentum/reversal blend. Two AI
  candidates are trend-conditioned pullbacks and volatility-scaled reversal.
  Same bounded long-only mapping for all scored signals, 1x gross, no leverage.
  Buy-and-hold comparators deliberately drift; inspect their concentration.
- Charge 5 bps per dollar bought or sold (fees, spread and slippage combined),
  stress at 10 bps. This is an assumption, not a measured IBKR execution cost.
  Debit initial purchases and terminal liquidation; account for drift and costs
  before calculating next holdings. No risk-free yield is assumed.
- Date-block uncertainty, paired baseline comparisons, yearly/sector attribution,
  rank IC and failure cases matter more than a winning historical Sharpe.
  All serious trials, including failures and reruns, remain in SQLite.
- IBKR Desktop is present; TWS/Gateway API listener and official SDK are absent.
  Execution boundary is read-only, with exact independently verified paper
  allowlist checks; every submission path rejects. Port/account prefix proves
  nothing. Broker availability must never block historical work.
  See [IBKR evidence](IBKR.md).
- Active competition research policy starts with equal weight unless evidence
  clears simple alternatives. Challengers stay separately versioned shadows.
  Saving fresh forecasts is permitted; retroactively calling a backtest forward
  is not. No contest portfolio has been submitted or filled.

## 2026-09-20, after first daily experiment

- Run `20260920T151942_ca864aa8`, snapshot `e9c58c170b0b65d53e00`:
  neither AI candidate beat equal weight on validation. Annual paired net active
  returns were -1.37% (trend pullback) and -2.26% (volatility-scaled reversal).
  Ordinary reversal's gross active return was +0.36%/year but its incremental
  cost drag was about 3.15%/year. Every score's validation IC interval spanned 0.
  Trend pullback beat the additive blend chiefly through lower turnover; that
  comparison alone is not evidence of a superior prediction mechanism.
- No candidate is promoted. Low volatility's slightly better CAGR comes with
  lower SPY beta; its paired active interval also spans zero. Retain the simple
  equal-weight active policy, with candidates and alternatives as shadows.
- Register ONE follow-up before running it: hold exactly the same rules between
  rebalances every five sessions, anchored at the first entry of each historical
  partition. This tests whether slower trading preserves the effect while reducing
  costs. No lookback/tilt tuning or cadence grid. Validation is now reused and must
  be labelled exploratory for this follow-up. The 2023+ holdout remains sealed.
- Defer a natural-language interface and ML. The immediate uncertainty concerns
  economics and persistence of a weak signal, not expressiveness or model capacity.

## 2026-09-20, after five-session diagnostic

- Run `20260920T152355_b81a3e5b` reused the same exposed discovery/validation.
  Slower turnover improved costs but did not establish an edge. Validation
  paired active returns vs the same-cadence equal-weight portfolio: -0.54%/year
  trend pullback, +0.09%/year volatility-scaled reversal; both intervals include
  zero and both lose at 10 bps. Trend pullback's apparent advantage over the blend
  shrank from +1.96% to +0.08%/year. This is a cost/persistence diagnosis, not
  independent validation. No cadence grid, model promotion, or holdout opening.
- Retain daily equal weight as the predeclared active hurdle; this is an
  intentionally equity-exposed sector portfolio, not claimed alpha. Small
  differences between daily/five-session equal weight do not justify selecting
  whichever performed best. Save all daily challengers as prospective shadows.
- Next highest-value work: verify the paper account and source/execution prices,
  begin the honest forward ledger, and obtain point-in-time corporate-action and
  broader equity coverage if available. More indicator combinations on nine
  correlated ETFs have lower expected value than improving observations.
- Skeptical review: the interaction has many tied scores, reducing active risk
  (daily validation tracking error about 1.83% vs the blend's 3.07%). Its
  improvement cannot isolate a nonlinear mechanism from weaker tilts and lower
  turnover. Weight caps constrain rebalance targets; between five-session
  rebalances observed weights can drift above 20% (reversal reached 21.31%).
  This does not create leverage, but must be visible when comparing exposures.

## 2026-09-20, milestone verification and first forecast

- Replay `20260920T152755_c48f60ae` exactly reproduces the initial daily summary,
  including deterministic block-bootstrap bounds. The ledger preserves all 54
  model/partition records: two nine-model studies plus the explicit replay.
- Frozen batch `batch_82b72b259653437c`, policy version
  `3e637985be0bfad1592c`, was issued at 2026-09-20 15:31:41 UTC for September 21
  13:30 UTC entry and September 22 13:30 UTC outcome. Active equal weight and
  eight shadow models are retained. No order, fill or account position exists.
- Current target liquidity diagnostic: the largest initial sector allocation is
  about 0.0191% of its prior 20-session mean dollar volume. This does not measure
  opening-auction capacity or prove equivalent fills. Target weights are 11.11%
  each; estimated trailing covariance is recorded with the forecast rather than
  treated as a guarantee about competition risk.
- Prediction completion is timestamped after feature calculation and checked
  again before persistence. Saved forecast/outcome file hashes detect accidental
  edits; first-observed outcomes are not replaced after provider revisions.
  Synthetic forward tests exercise pending, realized and revised-data paths.
- Removed the protocol's manually typed registration timestamp: it was not a
  clock-generated record. The initial config/protocol source archive and trial
  registration before scoring are the actual local evidence of predeclaration.
  They are not an externally timestamped preregistration. Existing archived
  protocols and the frozen forecast are preserved rather than rewritten.

## 2026-09-21, resumed Phase 2 and completed empirical screen

- Recovered the interrupted session and cached snapshot `291acec28122cd2ee6f8`.
  Retained 85 usable stocks from the 127 historical identities and 13 ETFs;
  preserved every exclusion and the survivor/availability-bias evidence limit.
- Discovery `20260921T173042_phase2_36e614` froze momentum top10 and QQQ
  pullback overnight before validation. A real discovery residual inspection
  generated `config/phase2_followup.json`; that deterministic AI rule failed.
- Validation attempt `20260921T173413_phase2_48b1f6` stopped on the fixed ADV
  limit. Completion `20260921T174553_phase2_a3bc02` retains the limit and both
  infeasible candidates (reversal/ridge). The continuation is not fresh evidence.
- No promotion: stock equal weight 11.39%, momentum 9.25%, AI follow-up 4.40%
  net validation CAGR. Volume interaction's apparent lead fails 10bps stress.
  Selected overnight loses 3.21%; initial 50/50 returns 3.77%. Time-sharing
  loses 27.64% at baseline costs. Do not add leverage to rescue these results.
- Preserve the original competition policy. Freeze `phase2_shadows_v1` with
  stock equal weight/cash references and named challengers, excluding historically
  infeasible reversal/ridge. Save the first overnight batch before Monday close.
  Do not fabricate Monday-opening stock targets; first stock anchor is Tuesday.
- Added exact modeled auction-dollar attribution and separate prediction-batch
  outcomes. Neither is a broker fill/account curve. All 84 tests pass. Account,
  contest permissions, terminal marking and scheduler remain unconfigured.
- Full evidence and operational commands: `docs/PHASE2_FINDINGS.md`, README,
  and the completed run report. Historical 2023+ outcomes remain sealed.

## 2026-09-21: new competition model, frozen final audit

- User explicitly suspended old sector execution and authorized a new compact chronological model study and one final holdout audit.
- Run `20260921T210708_working_model`: 95,928 purged training labels through 2017; reused validation 2018-2022. Ridge10 beat rule/boosting alternatives under the selection criterion; top10 equal sizing retained.
- Exact finalist frozen 2026-09-21T22:04:00Z; holdout opened once at 22:05:31Z. Ridge CAGR 46.98% (42.84% at 10bps); final gate passed. No post-audit search/refit.
- Overnight alternatives stayed in cash at the fixed cost hurdle; reject the sleeve. Allocation 100% aggressive book; 99.5% gross target plus 0.5% cash.
- Freeze `config/model_v1/COMPETITION_V1.json`, version `cbe43010220707651123`; preserve previous research policies. New forecast `model_3de7b630030340cd` targets September 22 opening.
- Generic target-to-PAPER adapter preserves account/mode/data/risk/idempotency/reconciliation safeguards. 129 tests pass. No TWS listener or exact paper allowlist; no submissions/fills.
- Negative validation rank IC, high beta, survivor/availability bias, overlapping windows, high turnover and historical drawdowns remain material limitations. Full report: `WORKING_MODEL_REPORT.md`.
