# Quant research pilot

Current model: **AGGRESSIVE_V1 / ridge10**, top 10 stocks, equal weights,
five-session rebalancing. The chronological final audit passed; overnight was
rejected. The old sector policy is suspended. See [WORKING_MODEL_REPORT.md](WORKING_MODEL_REPORT.md)
for results and frozen targets. The current commissioning procedure is
[PAPER opening readiness](docs/AUTONOMOUS_PAPER_OPEN.md).

The PAPER adapter now requires an explicit, immutable single-batch arm. The
watcher performs no research or forecast generation. The one-time September 28
task starts a **readiness-only** check at 05:45 Vancouver. Monday is an exchange
session but is off the frozen five-session cadence anchored September 22; the
next allowed rebalance is September 29 using completed September 28 data.
`config/paper_schedule.json` has no selected batch and cannot submit orders.
Actual readiness is recorded by
`paper_cli readiness` in `state/paper_checks/TOMORROW_PREOPEN_READINESS.json`;
installation alone does not authorize orders. CAD account valuation requires
live USD/CAD conversion, and USD stock buys require actual USD cash funding.
No commissioning orders are sent.
Earlier milestone descriptions below are retained as historical context.

**Phase 2 is complete:** [findings](docs/PHASE2_FINDINGS.md) and
[empirical report](runs/20260921T174553_phase2_a3bc02/report.md). The 85-stock
screen, overnight tests, AI follow-up, 50/50 attribution and capital time-sharing
diagnostic did not establish a promotable edge. The original competition policy
is unchanged; historical 2023+ outcomes remain sealed.

The first Phase 2 overnight shadow is frozen for September 21 close → September
22 open. Phase 2 is a separate shadow experiment with stock equal weight and
cash references. After each completed session, after **14:00 Vancouver /
17:00 New York**, run:

```powershell
.\.venv\Scripts\python.exe -m quantlab phase2-fetch
.\.venv\Scripts\python.exe -m quantlab phase2-evaluate
.\.venv\Scripts\python.exe -m quantlab phase2-predict --sleeve overnight
```

After September 21 data is available, also freeze the first stock batch before
**06:28 Vancouver / 09:28 New York on September 22**:

```powershell
.\.venv\Scripts\python.exe -m quantlab phase2-predict --sleeve aggressive
```

Stock rebalances start September 22, then occur every five trading sessions;
off-schedule calls are rejected. Late, stale, duplicate and modified-policy
forecasts are rejected too. These are reference targets, not executable orders.
The evaluator reports separate batch outcomes, not a funded account curve.
Continue the original sector workflow below too. No scheduler is installed.

Reproduce Phase 2 offline with the frozen discovery choices and AI follow-up:

```powershell
.\.venv\Scripts\python.exe -m quantlab phase2 --snapshot 291acec28122cd2ee6f8 --parent 20260921T173042_phase2_36e614 --hypothesis config/phase2_followup.json
.\.venv\Scripts\python.exe -m quantlab phase2-attribution
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Omitting `--parent` runs discovery only. Every attempt is archived; failed
liquidity checks remain failed trials. Detailed dollar holdings and sleeve
reconciliation are under the completed run's `portfolio/*/auction_attribution/`.

The daily study was rerun on its original snapshot and its numerical summary
(including bootstrap bounds) matched exactly. Temporal alignment, accounting,
data integrity, forward-record integrity and broker safety have automated tests.

The first screen uses 54,620 real daily bars for nine sector ETFs plus SPY,
2005-01-03 through 2026-09-18. It tests nine explicit rules on discovery
2006–2017 and validation 2018–2022. Historical performance from 2023 onward is
reserved; recent prices are used only for current features/risk and future
predictions. Raw vendor responses, data hashes, configs, executable source and
all serious attempts are retained locally.

**Evidence so far: neither AI candidate earns promotion.** Daily equal weight
earned 10.02% net annualized in validation, trend pullback 8.50%, and
volatility-scaled reversal 7.30%, at an assumed 5 bps per dollar traded. A
five-session rebalance diagnostic reduced costs but found no robust incremental
edge. These are historical screening results, not expected future returns.

- [First empirical report](runs/20260920T151942_ca864aa8/report.md)
- [Five-session results](runs/20260920T152355_b81a3e5b/report.md) and
  [why this is an adaptive diagnostic](runs/20260920T152355_b81a3e5b/INTERPRETATION.md)
- [Findings, failures and limitations](docs/FINDINGS.md)
- [Decisions and reasoning](docs/DECISIONS.md)
- [IBKR setup and safeguards](docs/IBKR.md)

## Run the next experiment now

From this repository in PowerShell, using the already installed environment:

```powershell
.\.venv\Scripts\python.exe -m quantlab run --snapshot e9c58c170b0b65d53e00
.\.venv\Scripts\python.exe -m quantlab history
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

The first command works offline and prints the new `runs/<id>` directory.
Open `report.md`, `summary.csv`, `equity.png`, `yearly.csv`,
`sector_contributions.csv` and `failures.csv`. Each model/partition also contains
`scores.csv`, `targets.csv`, `weights.csv`, `daily.csv` and `trades.csv`.
Historical trades are simulations, not broker fills.

To rerun an exact prior configuration on its original data:

```powershell
.\.venv\Scripts\python.exe -m quantlab run --config runs/20260920T151942_ca864aa8/config.json --snapshot e9c58c170b0b65d53e00
```

Each run archives its code under `source/`, environment versions in
`manifest.json`, and data references in `data_manifest.json`. After code changes,
use an isolated checkout of that archived source to reproduce the old code too;
copy the cached `data/` directory into that checkout. The command above on current
code records its new source hash instead of pretending it used the old version.
Failed runs retain `error.txt` and failed/not-run trial records in SQLite.

For a new experiment, copy a config and give it a new study name and a written
reason. Do not tune on the reserved history. `five_session_study.json` shows a
small, interpretable change. New signal rules belong in `make_signals` in
`quantlab/core.py` and the model allowlist in `experiment.py`, with a provenance
label and matched baseline in the config. Test future-price invariance before
using a new feature. There is no arbitrary strategy-execution DSL.

## Prospective competition workflow

The active research policy is `config/competition_v1.json`: daily equal weight,
long-only, 1x gross, $1,000,000 reference notional. Other daily rules and SPY/
equal-weight buy-and-hold are shadows. This deliberately retains equity-market
risk; it does not claim alpha or represent an already funded/filled portfolio.
Actual competition rules and the paper account are still unverified.

The first prospective batch is already frozen: [Monday September 21 forecast](state/forward/batch_82b72b259653437c/forecast.json)
and [reference position intents](state/forward/batch_82b72b259653437c/position_intents.csv),
issued September 20 at 15:31 UTC. It targets the September 21 opening session
and the September 22 opening outcome. All outcomes are still pending; no
submission, fill or observed account position exists. Do not regenerate this
same policy/session: its original predictions must remain unchanged.

After each completed US session, normally after **17:00 New York / 14:00
Vancouver**, or before **09:28 New York / 06:28 Vancouver** the next session:

```powershell
.\.venv\Scripts\python.exe -m quantlab fetch
.\.venv\Scripts\python.exe -m quantlab evaluate-forward
.\.venv\Scripts\python.exe -m quantlab predict
```

`fetch` makes ten public requests and creates a new immutable snapshot.
`evaluate-forward` freezes first-observed outcomes for earlier forecasts and
reports separate shadow curves; it cannot score a forecast whose outcome is
still in the future. `predict` saves a batch before its next opening execution
time, including scores, target weights, reference dollars, source version and
risk/liquidity diagnostics. Initial share estimates use the last close and
**are not executable orders**. A real sizing step must reconcile actual account
NAV/holdings, opening prices, whole shares and fees before any paper execution.

The ledger rejects stale bars, late issuance, duplicate policy/session batches,
changed frozen files and silent policy changes. To change a strategy, use a new
policy name and record the reason; previous forecasts remain intact. Source
changes affecting prediction require a new policy version. The raw forecast and
outcome hashes detect accidental edits; this local store is not tamper-proof
against its owner. Back up `data/`, `runs/` and `state/`.

Predictions, position intents, submitted orders, fills and observed broker
positions have separate SQLite tables. The last three are empty until actual
broker integration is configured and implemented. Do not enter simulated fills
into those tables. No scheduler is installed: the three-command workflow must
run each session. Gaps in a shadow history produce an error rather than silently
inventing intervening returns. The assumed competition end is October 20;
confirm the contest's exact finish before final liquidation/evaluation. Current
forward curves exclude terminal liquidation; a finalization step must be added
once the final trading/marking time is known.

## What makes the experiments auditable

- Final close at session t, with a conservative one-hour publication lag;
  enter open t+1 and mark/exit open t+2. No same-close fills. One-session labels
  do not overlap. Outcomes crossing partition boundaries are purged.
- All models use exactly matched dates. Positive lookbacks are enforced and
  future-price perturbation tests verify signals cannot read subsequent rows.
- Scored rules map to bounded rank tilts. Targets are capped at 20%; holdings
  can drift above the target cap between scheduled trades. Buy-and-hold/SPY
  references deliberately have different concentration, reported explicitly.
- Costs debit every dollar bought and sold, including initial purchases and
  terminal liquidation. Drift and post-cost sizing reconcile to sector P&L.
  Base/stress assumptions are 5/10 bps; they are not measured fill costs.
- Confidence intervals resample dates in 20-session blocks, not individual
  ETF rows. They are exploratory and unadjusted for search. Reports expose
  prior attempts, matched ablations, cost drag, beta, IC and loss cases.

## Dependencies and data limits

For a fresh machine with Python 3.13:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m quantlab fetch --end 2026-09-18
```

Research uses NumPy, pandas, exchange-calendars, requests and matplotlib. Local
CSV/JSON snapshots and SQLite are sufficient for this scale. No paid API,
LLM API, broker, web server or GPU is required.

Yahoo's public chart endpoint is undocumented and can fail/change. Adjusted-open
returns are a dividend-reinvestment proxy from revised history, not exact
historical executable prices or a point-in-time corporate-action ledger. The
fund universe reduces current-stock constituent bias but has survivor and
sector-definition limitations. Nine correlated ETFs and one forward month
cannot establish general AI alpha. [Yahoo adjustments](https://help.yahoo.com/kb/SLN28256.html),
[issuer history](https://www.ssga.com/mainfund/XLK),
[NYSE sessions](https://www.nyse.com/trade/hours-calendars).

Next work should improve observations: verify the competition paper account and
opening-fill workflow, preserve daily forecasts, and assess available timestamped
equity/corporate-action data. A natural-language hypothesis interface, textual
features and ML stay deferred until they address a demonstrated research gap.
